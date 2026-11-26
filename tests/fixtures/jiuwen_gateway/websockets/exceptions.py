class ConnectionClosed(Exception):
    def __init__(self, rcvd=None, sent=None):
        super().__init__("connection closed")
