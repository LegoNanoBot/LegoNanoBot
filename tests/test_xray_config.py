"""Tests for X-Ray configuration."""

from nanobot.config.schema import XRayConfig


def test_xray_config_defaults():
    """XRayConfig 默认值正确"""
    cfg = XRayConfig()
    assert cfg.enabled is False
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 9100
    assert cfg.db_path == ".nanobot/xray.db"
    assert cfg.retention_hours == 72
    assert cfg.max_message_size == 32768


def test_xray_config_custom():
    """XRayConfig 自定义值"""
    cfg = XRayConfig(enabled=True, port=8080, retention_hours=24)
    assert cfg.enabled is True
    assert cfg.port == 8080
    assert cfg.retention_hours == 24


def test_xray_config_host_override():
    """XRayConfig host 可以自定义"""
    cfg = XRayConfig(host="0.0.0.0")
    assert cfg.host == "0.0.0.0"


def test_xray_config_db_path_override():
    """XRayConfig db_path 可以自定义"""
    cfg = XRayConfig(db_path="/custom/path/xray.db")
    assert cfg.db_path == "/custom/path/xray.db"


def test_xray_config_max_message_size_override():
    """XRayConfig max_message_size 可以自定义"""
    cfg = XRayConfig(max_message_size=65536)
    assert cfg.max_message_size == 65536
