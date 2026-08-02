import os


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'jingcai-dev-secret-2024')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL', 'sqlite:///jingcai.db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REFRESH_INTERVAL = 120
    BZZOIRO_API_KEY = os.environ.get('BZZOIRO_API_KEY', '')
