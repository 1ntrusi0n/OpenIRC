"""Structured protocol errors contain safe public text, never credentials."""


class IRCError(Exception):
    def __init__(self, code: int, text: str, params: tuple[str, ...] = ()):
        super().__init__(text)
        self.code = code
        self.text = text
        self.params = params
