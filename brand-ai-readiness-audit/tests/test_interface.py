import http.client
import threading

from app import create_server


def test_server_serves_interface():
    server = create_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/")
        response = conn.getresponse()
        body = response.read().decode("utf-8")

        assert response.status == 200
        assert "ai-readiness audit" in body.lower()
        assert "url" in body.lower()
    finally:
        server.shutdown()
        server.server_close()
