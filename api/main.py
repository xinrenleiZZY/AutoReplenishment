"""API Docker入口 - 导入已有的 app.main 并添加前端所需配置"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
