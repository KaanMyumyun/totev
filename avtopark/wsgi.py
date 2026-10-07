"""WSGI entry point for production servers: gunicorn wsgi:app"""
from avtopark import create_app

app = create_app()
