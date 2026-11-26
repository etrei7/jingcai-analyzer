// 离线校验 index.html：① 每个内联 <script> 能通过 JS 解析 ② Edge 差值榜渲染逻辑正确
// 用法：node tools/check_edge_board_ui.js   （无需网络/浏览器）
const fs = require('fs');
const path = require('path');

const ROOT = path.dirname(__dirname);
const html = fs.readFileSync(path.join(ROOT, 'templates', 'index.html'), 'utf8');

let fail = 0;
function check(name, cond, detail) {
  if (cond) console.log('  OK   ' + name);
  else { fail++; console.log('  FAIL ' + name + ' ' + (detail === undefined ? '' : String(detail).slice(0, 200))); }
}

// ---- ① 内联脚本语法 ----
const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)(?![^>]*application\/ld\+json)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
check('找到内联脚本', scripts.length > 0, scripts.length);
let parseErr = '';
let total = 0;
scripts.forEach((code, i) => {
  if (!code.trim()) return;
  total += code.length;
  try { new Function(code); } catch (e) { if (!parseErr) parseErr = 'script#' + i + ': ' + e.message; }
});
check('内联脚本全部可解析（共 ' + scripts.length + ' 段 / ' + total + ' 字符）', parseErr === '', parseErr);

// ---- ② 面板结构存在 ----
check('HTML 含 edgeBoard 容器', /id="edgeBoard"/.test(html));
check('CSS 含 .eb-row 网格', /\.eb-row\{display:grid/.test(html));
check('showData 已接线 renderEdgeBoard', /renderEdgeBoard\(data\.edge_board\)/.test(html));

// ---- ③ 渲染逻辑（stub DOM） ----
const start = html.indexOf('// ===== Edge 差值榜');
const end = html.indexOf('function showData(data){');
check('渲染函数段落可定位', start > 0 && end > start, start + '/' + end);
const renderSrc = html.slice(start, end);

const escStart = html.indexOf('function esc(s) {');
const escEnd = html.indexOf('function rankClass(r)');
const escSrc = html.slice(escStart, escEnd);

const box = { style: {}, innerHTML: '' };
const list = { innerHTML: '' };
const meta = { textContent: '' };
const note = { innerHTML: '' };
const nodes = { edgeBoard: box, edgeBoardList: list, edgeBoardMeta: meta, edgeBoardNote: note };
const documentStub = { getElementById: id => nodes[id] || null };

const esc = new Function(escSrc + '; return esc;')();
const renderEdgeBoard = new Function('document', 'esc', renderSrc + '; return renderEdgeBoard;')(documentStub, esc);

const board = JSON.parse(fs.readFileSync(path.join(ROOT, 'tools', 'fixtures', 'edge_board.json'), 'utf8'));
renderEdgeBoard(board);
check('渲染后容器可见', box.style.display === '', box.style.display);
check('元信息含评估/可看场次', /评估 3 场/.test(meta.textContent) && /可看 1 场/.test(meta.textContent), meta.textContent);
check('行数正确', (list.innerHTML.match(/class="eb-row"/g) || []).length === 3, (list.innerHTML.match(/class="eb-row"/g) || []).length);
check('可看行有 ok 徽标', /eb-badge ok/.test(list.innerHTML));
check('仅观察行有 watch 样式', /eb-pick watch/.test(list.innerHTML));
check('Edge 带正负号', /\+7\.5/.test(list.innerHTML));
check('同类命中率已渲染', /同类 48\.0%/.test(list.innerHTML), list.innerHTML.slice(0, 300));
check('Wilson 95% 区间两行显示', /40-57% · n=120/.test(list.innerHTML), list.innerHTML.slice(0, 300));
check('置信区间与 ROI 带写入 title', /95% 区间 39\.6-56\.5%/.test(list.innerHTML) && /ROI -7\.8%/.test(list.innerHTML), list.innerHTML.slice(0, 500));
check('校准面板命中率列用 CI 区间', /d\.ci_low!=null&&d\.ci_high!=null/.test(html) && /Wilson 95%/.test(html));
check('需下注门槛写入页脚', /赔率 ≤ 8/.test(note.innerHTML), note.innerHTML);
check('前视风险提示写入页脚', /前视/.test(note.innerHTML));
check('行可点击跳转（data-mid + dbJump）', /data-mid="周日001"/.test(list.innerHTML) && /onclick="dbJump\(this\)"/.test(list.innerHTML));

renderEdgeBoard({ rows: [], summary: { evaluated: 0, actionable: 0 }, note: '空' });
check('空榜给出正常空态', /正常空态/.test(list.innerHTML), list.innerHTML.slice(0, 120));

renderEdgeBoard(null);
check('无数据隐藏面板', box.style.display === 'none', box.style.display);

console.log('RESULT:', fail ? 'FAIL' : 'ALL OK');
process.exit(fail ? 1 : 0);
