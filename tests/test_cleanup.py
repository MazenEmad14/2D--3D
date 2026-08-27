import os
import time
import tempfile
from pathlib import Path
from app.engine.cleanup import cleanup_expired_glbs

def test_cleanup_expired_glbs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        
        # Create a "fresh" glb file
        fresh_file = tmp_path / "fresh.glb"
        fresh_file.touch()
        
        # Create an "expired" glb file
        old_file = tmp_path / "old.glb"
        old_file.touch()
        
        # Artificially age the old file by 2 hours
        two_hours_ago = time.time() - (2 * 60 * 60)
        os.utime(old_file, (two_hours_ago, two_hours_ago))
        
        # Run cleanup with 60-minute TTL
        deleted_count = cleanup_expired_glbs(tmp_path, ttl_minutes=60)
        
        # Assertions
        assert deleted_count == 1
        assert not old_file.exists(), "Old file should be deleted"
        assert fresh_file.exists(), "Fresh file should be preserved"
