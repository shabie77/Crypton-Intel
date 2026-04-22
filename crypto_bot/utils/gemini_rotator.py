import asyncio
import os


class APIKeyRotator:
    def __init__(self, env_var_multiple, env_var_single=None):
        raw_keys = os.getenv(env_var_multiple, "")
        self.keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        
        if not self.keys and env_var_single:
            single = os.getenv(env_var_single)
            if single:
                self.keys = [single.strip()]

        self.current_index = 0
        self.lock = asyncio.Lock()

    async def reserve_key(self):
        async with self.lock:
            if not self.keys:
                return None, None
            index = self.current_index % len(self.keys)
            key = self.keys[index]
            self.current_index = (index + 1) % len(self.keys)
            return index, key

    async def advance_after_rate_limit(self, reserved_index):
        async with self.lock:
            if not self.keys:
                return
            # Force advance to next key
            self.current_index = (self.current_index + 1) % len(self.keys)

    def has_keys(self):
        return len(self.keys) > 0

    def get_all_keys(self):
        return self.keys


class GeminiRotator(APIKeyRotator):
    def __init__(self, keys_str=None):
        # Allow passing keys directly for testing, otherwise use env vars
        if keys_str:
            self.keys = [k.strip() for k in keys_str.split(",") if k.strip()]
            self.current_index = 0
            self.lock = asyncio.Lock()
        else:
            super().__init__("GEMINI_KEYS", "GEMINI_API_KEY")


class GroqRotator(APIKeyRotator):
    def __init__(self):
        super().__init__("GROQ_KEYS", "GROQ_API_KEY")
