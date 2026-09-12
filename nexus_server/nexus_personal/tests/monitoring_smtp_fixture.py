"""Bounded loopback SMTP peer; actual smtplib protocol, no external recipients."""
import socketserver
import threading


class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        self.wfile.write(b"220 test SMTP\r\n")
        while True:
            line = self.rfile.readline(8193)
            if not line or len(line) > 8192:
                return
            command = line.split(b" ", 1)[0].strip().upper()
            if command == b"QUIT":
                self.wfile.write(b"221 Bye\r\n")
                return
            if command in (b"EHLO", b"HELO", b"MAIL", b"RSET", b"NOOP"):
                self.wfile.write(b"250 OK\r\n")
            elif command == b"RCPT":
                self.wfile.write(b"550 Rejected\r\n" if self.server.reject else b"250 OK\r\n")
            elif command == b"DATA":
                self.wfile.write(b"354 End with dot\r\n")
                content = bytearray()
                while True:
                    chunk = self.rfile.readline(8193)
                    if not chunk or len(chunk) > 8192:
                        return
                    if chunk == b".\r\n":
                        break
                    content.extend(chunk)
                    if len(content) > 262144:
                        return
                with self.server.messages_lock:
                    self.server.messages.append(bytes(content))
                self.wfile.write(b"250 Accepted\r\n")
            else:
                self.wfile.write(b"502 Unsupported\r\n")


class SMTPFixture:
    def __enter__(self):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTPHandler)
        self.server.daemon_threads = True
        self.server.reject = False
        self.server.messages, self.server.messages_lock = [], threading.Lock()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.server

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
