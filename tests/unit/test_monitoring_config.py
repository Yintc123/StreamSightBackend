"""monitoring_* 設定預設值（monitoring.md §4.1）。"""

from app.core.config import BaseAppSettings, get_app_settings


def test_monitoring_config_defaults() -> None:
    settings: BaseAppSettings = get_app_settings()

    assert settings.monitoring_enabled is True
    assert settings.monitoring_log_stream_maxlen == 10000
    assert settings.monitoring_log_queue_maxsize == 1000
    assert settings.monitoring_log_flush_interval_seconds == 1
    assert settings.monitoring_log_flush_batch_size == 100
    assert settings.monitoring_log_push_enabled is False
    assert settings.monitoring_db_sample_interval_seconds == 15
    assert settings.monitoring_db_stream_maxlen == 10000
    assert settings.monitoring_sampler_leader_lease_seconds == 30
    assert settings.monitoring_query_max_limit == 500
    assert settings.monitoring_retention_seconds == 604800


def test_monitoring_sampler_lease_gte_twice_interval() -> None:
    """leader lease 須 ≥ 2× 採樣週期（monitoring.md §2.5）。"""
    settings: BaseAppSettings = get_app_settings()
    assert settings.monitoring_sampler_leader_lease_seconds >= (
        2 * settings.monitoring_db_sample_interval_seconds
    )


def test_monitoring_infra_config_defaults() -> None:
    """infra-monitoring.md §4.3 預設值驗證。"""
    settings: BaseAppSettings = get_app_settings()

    assert settings.monitoring_infra_enabled is True
    assert settings.monitoring_infra_node_exporter_url == "http://node-exporter:9100"
    assert settings.monitoring_infra_mysqld_exporter_url == "http://mysqld-exporter:9104"
    assert settings.monitoring_infra_interval_seconds == 5
    assert settings.monitoring_infra_retention_hours == 24
    assert settings.monitoring_infra_default_query_hours == 1
    assert settings.monitoring_infra_redis_key == "monitoring:infra:history"
