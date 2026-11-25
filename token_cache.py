import json
import os
from pathlib import Path
import stat
import tempfile


class TokenCache:
    @classmethod
    def read(cls):
        try:
            descriptor = os.open(cls.SKETCHFAB_CACHE_FILE, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return {}

        with os.fdopen(descriptor, encoding='utf-8') as cache:
            if hasattr(os, 'fchmod') and stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
                os.fchmod(descriptor, 0o600)
            data = json.load(cache)
        if not isinstance(data, dict):
            raise ValueError('Invalid Sketchfab cache')
        return data

    @classmethod
    def get_key(cls, key):
        return cls.read().get(key)

    @classmethod
    def write(cls, data):
        destination = Path(cls.SKETCHFAB_CACHE_FILE)
        descriptor, temporary = tempfile.mkstemp(prefix='.cache-', dir=destination.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as cache:
                json.dump(data, cache)
            os.replace(temporary, destination)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @classmethod
    def save_key(cls, key, value):
        try:
            data = cls.read()
        except ValueError:
            data = {}
        data[key] = value
        cls.write(data)

    @classmethod
    def delete_keys(cls, *keys):
        try:
            data = cls.read()
        except ValueError:
            cls.write({})
            return
        if any(key in data for key in keys):
            for key in keys:
                data.pop(key, None)
            cls.write(data)
