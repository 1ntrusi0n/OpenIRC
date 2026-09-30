"""Argon2id credentials; expensive operations never occupy the network loop."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

_DEFAULT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="OpenIRC-credential")
_DEFAULT_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, type=Type.ID)


class PasswordService:
    def __init__(self) -> None:
        self._hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, type=Type.ID)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="OpenIRC-password")
        self._gate = asyncio.Semaphore(8)
        self._closed = False

    async def hash(self, password: str) -> str:
        if not isinstance(password, str) or not 1 <= len(password.encode("utf-8")) <= 1024:
            raise ValueError("Credentials must contain between 1 and 1024 UTF-8 bytes")
        return await self._run(self._hasher.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        if not isinstance(password, str) or len(password.encode("utf-8")) > 1024:
            return False
        return await self._run(self._verify, password_hash, password)

    def _verify(self, password_hash: str, password: str) -> bool:
        try:
            return bool(self._hasher.verify(password_hash, password))
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            return False

    async def _run(self, function, *args):
        if self._closed:
            raise RuntimeError("Password service is closed")
        async with self._gate:
            return await asyncio.get_running_loop().run_in_executor(self._executor, partial(function, *args))

    async def close(self) -> None:
        self._closed = True
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)


async def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 1 <= len(password.encode("utf-8")) <= 1024:
        raise ValueError("Credentials must contain between 1 and 1024 UTF-8 bytes")
    return await asyncio.get_running_loop().run_in_executor(_DEFAULT_EXECUTOR, _DEFAULT_HASHER.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    if not isinstance(password, str) or len(password.encode("utf-8")) > 1024:
        return False
    def verify():
        try:
            return bool(_DEFAULT_HASHER.verify(password_hash, password))
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            return False
    return await asyncio.get_running_loop().run_in_executor(_DEFAULT_EXECUTOR, verify)
