import socket
import json

def get_local_broadcast():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        parts = ip.split('.')
        parts[-1] = '255'
        return '.'.join(parts)
    except Exception:
        return '255.255.255.255'

class UDPNetwork:
    def __init__(self, port=9999, broadcast_ip=None, self_guid=None):
        self.port = port
        self.broadcast_ip = broadcast_ip or get_local_broadcast()
        self.self_guid = self_guid
        
        # Setup sender socket
        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        # Setup receiver socket
        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
            
        self.recv_sock.bind(('', self.port))
        self.recv_sock.setblocking(False)
        
    def send_payload(self, payload: dict):
        try:
            if self.self_guid and "source_guid" not in payload:
                payload["source_guid"] = self.self_guid
            data = json.dumps(payload).encode('utf-8')
            self.send_sock.sendto(data, (self.broadcast_ip, self.port))
        except Exception as e:
            print(f"Failed to send payload: {e}")

    def receive_payloads(self):
        """Non-blocking read of all available payloads"""
        payloads = []
        while True:
            try:
                data, addr = self.recv_sock.recvfrom(65535)
                try:
                    payload = json.loads(data.decode('utf-8'))
                    # Ignore payloads sent by ourselves if source_guid matches
                    if self.self_guid and payload.get("source_guid") == self.self_guid:
                        continue
                    payloads.append(payload)
                except json.JSONDecodeError:
                    pass
            except BlockingIOError:
                break
            except Exception as e:
                print(f"Error receiving payload: {e}")
                break
        return payloads

    def close(self):
        self.send_sock.close()
        self.recv_sock.close()

    def stop(self):
        self.close()
