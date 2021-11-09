# Copyright (c) 2021 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-1-Clause
import sys
import json
import rpc


class StreamEnd(Exception):
    pass


class MockStream:
    def __init__(self):
        self.input = b""
        self.output = b""

    def read(self, n=-1):
        if len(self.input) < n:
            raise StreamEnd()

        if n == -1:
            retval = self.input
            self.input = b""
        else:
            retval = self.input[:n]
            self.input = self.input[n:]
        return retval

    def readline(self):
        try:
            idx = self.input.index(b"\n")
        except ValueError as e:
            raise StreamEnd()
        val = self.read(idx + 1)
        sys.stdout.write(f'Reading line as {idx} bytes: "{val}"\n')
        return val

    def write(self, buf: bytes):
        self.output += buf

    def push(self, buf: str, encoding="utf-8"):
        self.input += buf.encode(encoding)

    def flush(self):
        pass

    def pull(self, n=-1):
        if n == -1 or n > len(self.output):
            retval = self.output
            self.output = b""
        else:
            retval = self.output[:n]
            self.output = self.output[n:]
        return retval.decode("utf-8")

    def pull_line(self):
        try:
            idx = self.output.index(b"\n")
        except ValueError as e:
            raise StreamEnd()
        return self.pull(idx + 1)

    def recv(self):
        headers = {}
        for _ in range(4):  # max header count
            header = self.pull_line().strip()
            if len(header) == 0:  # blank header marks the end
                break
            [key, value] = header.split(":", 2)
            headers[key.strip()] = value.strip()
        else:
            raise EOFError("Expected headers to end")

        length = int(headers["Content-Length"])
        obj = json.loads(self.pull(length))
        return rpc.RPCMsg.from_obj(obj)

    def send(self, msg: rpc.RPCMsg):
        content = str(msg)
        self.push(
            rpc.LINE_ENDING.join([f"Content-Length: {len(content)}", "", content])
        )
