"""TCP fixtures synchronize with protocol replies, not arbitrary sleeps."""
import asyncio
import uuid


class IRCClient:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def connect(cls, server, nick, *, ircx=False, ssl=None, port=None):
        address = server.bound_addresses[0]
        reader, writer = await asyncio.open_connection(address[0], port or address[1], ssl=ssl)
        client = cls(reader, writer)
        if ircx:
            await client.send("IRCX")
        await client.send(f"NICK {nick}", f"USER {nick} 0 * :{nick} Test")
        await client.until(" 376 ")
        return client

    async def send(self, *lines):
        self.writer.write(("\r\n".join(lines) + "\r\n").encode())
        await self.writer.drain()

    async def until(self, text, timeout=3):
        lines = []
        async with asyncio.timeout(timeout):
            while True:
                line = (await self.reader.readline()).decode().rstrip("\r\n")
                if not line:
                    raise AssertionError(f"Connection ended waiting for {text!r}; received {lines}")
                lines.append(line)
                if text in line:
                    return lines

    async def barrier(self):
        token = "barrier-" + uuid.uuid4().hex
        await self.send("PING :" + token)
        return await self.until(token)

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()
