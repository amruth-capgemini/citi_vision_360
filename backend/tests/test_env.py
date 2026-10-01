import os

from citi_project import env


def test_load_env_reads_nearest_file_and_keeps_process_values(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("CITI_TEST_FROM_FILE=file\nCITI_TEST_SET=file\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("CITI_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("CITI_TEST_SET", "process")
    try:
        assert env.load_env() == str(tmp_path / ".env")
        assert os.environ["CITI_TEST_FROM_FILE"] == "file"
        assert os.environ["CITI_TEST_SET"] == "process"  # an explicit variable wins over .env
    finally:
        monkeypatch.delenv("CITI_TEST_FROM_FILE", raising=False)


def test_load_env_falls_back_to_backend_file(tmp_path, monkeypatch):
    backend = tmp_path / "backend.env"
    backend.write_text("CITI_TEST_FALLBACK=backend\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "find_dotenv", lambda usecwd: "")
    monkeypatch.setattr(env, "BACKEND_ENV", backend)
    monkeypatch.delenv("CITI_TEST_FALLBACK", raising=False)
    try:
        assert env.load_env() == str(backend)
        assert os.environ["CITI_TEST_FALLBACK"] == "backend"
        monkeypatch.setattr(env, "BACKEND_ENV", tmp_path / "missing.env")
        assert env.load_env() is None
    finally:
        monkeypatch.delenv("CITI_TEST_FALLBACK", raising=False)
