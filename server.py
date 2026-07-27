import os

from server import app, socketio

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    use_reloader = os.environ.get('FLASK_USE_RELOADER', 'false').lower() == 'true'
    socketio.run(app, debug=True, use_reloader=use_reloader, port=5000, allow_unsafe_werkzeug=True)
