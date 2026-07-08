import os

from server import app, socketio

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
