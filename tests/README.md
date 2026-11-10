# Login tests

Run worker tests without Blender:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Run the integration script in a separate Blender instance with temporary user
paths. It uses a local fake API and synthetic credentials, then quits Blender.
It must not run in your normal Blender configuration.

```sh
test_dir=$(mktemp -d)
mkdir -p "$test_dir/scripts" "$test_dir/config"
SKFB_TEST_ROOT="$test_dir" \
BLENDER_USER_SCRIPTS="$test_dir/scripts" \
BLENDER_USER_CONFIG="$test_dir/config" \
/path/to/blender --factory-startup --python tests/blender_cached_login.py
```

Check `results.json` in the test directory: Blender may exit successfully even
when a Python assertion fails. Add `--background` to verify automatic login is
suppressed in background sessions; that mode prints `BACKGROUND_LOGIN_SUPPRESSED`.
