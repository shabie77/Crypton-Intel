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
        # Track cooldown for each key: {index: timestamp}
        self.cooldowns = {}

    async def reserve_key(self):
        import time
        async with self.lock:
            if not self.keys:
                return None, None
            
            num_keys = len(self.keys)
            # Try to find a key that is not on cooldown
            for _ in range(num_keys):
                index = self.current_index % num_keys
                key = self.keys[index]
                self.current_index = (self.current_index + 1) % num_keys
                
                # Check cooldown (60 seconds)
                last_429 = self.cooldowns.get(index, 0)
                if time.time() - last_429 > 60:
                    return index, key
            
            # If all keys are on cooldown, return the least-recently used one anyway
            index = self.current_index % num_keys
            return index, self.keys[index]

    async def advance_after_rate_limit(self, reserved_index):
        import time
        async with self.lock:
            if not self.keys:
                return
            # Mark this key as cooled down
            self.cooldowns[reserved_index] = time.time()
            
            # If we were already pointing at this key, move to next
            if self.current_index % len(self.keys) == reserved_index:
                self.current_index = (reserved_index + 1) % len(self.keys)

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
            self.cooldowns = {}
        else:
            super().__init__("GEMINI_KEYS", "GEMINI_API_KEY")


class GroqRotator(APIKeyRotator):
    def __init__(self):
        super().__init__("GROQ_KEYS", "GROQ_API_KEY")
