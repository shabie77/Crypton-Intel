import asyncio
import os


class GeminiRotator:
    def __init__(self, keys=None):
        self.keys = keys or os.getenv("GEMINI_KEYS", "").split(",")
        self.keys = [k.strip() for k in self.keys if k.strip()]
        if not self.keys and os.getenv("GEMINI_API_KEY"):
            self.keys = [os.getenv("GEMINI_API_KEY")]

        self.current_index = 0
        self.lock = asyncio.Lock()

    async def reserve_key(self):
        async with self.lock:
            if not self.keys:
                return None, None
            index = self.current_index % len(self.keys)
            key = self.keys[index]
            # Advance immediately so concurrent callers spread across keys.
            self.current_index = (index + 1) % len(self.keys)
            return index, key

    async def advance_after_rate_limit(self, reserved_index):
        async with self.lock:
            if not self.keys:
                return
            # Force advance if a key hit 429, ensuring we move past it in the sequence.
            self.current_index = (self.current_index + 1) % len(self.keys)
            print(f"Gemini key rotated due to 429. Next global start index: {self.current_index}")

    def has_keys(self):
        return len(self.keys) > 0

    def get_all_keys(self):
        return self.keys
