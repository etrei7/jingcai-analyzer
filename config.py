import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'jingcai-dev-secret-2024')
    # 使用绝对路径，确保 Web 进程(uWSGI)与 Bash/定时任务读取同一数据库文件，
    # 避免因工作目录不同导致读不到已写入的数据。
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'jingcai.db')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REFRESH_INTERVAL = 120
    BZZOIRO_API_KEY = os.environ.get('BZZOIRO_API_KEY', '')
