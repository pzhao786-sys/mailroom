import copy

import pytest
import yaml
from pydantic import ValidationError

from mailroom.config.schema import TenantConfig
from mailroom.tenant import TENANTS_DIR, TenantConfigError, list_tenants, load_tenant


def raw(slug="larkspur_freight"):
    return yaml.safe_load((TENANTS_DIR / slug / "tenant.yaml").read_text(encoding="utf-8"))


def test_demo_tenants_load(tmp_path):
    assert set(list_tenants()) >= {"larkspur_freight", "juniper_dental"}
    for slug in ("larkspur_freight", "juniper_dental"):
        t = load_tenant(slug, var_root=tmp_path)
        assert "other" in t.config.categories
        for cat, spec in t.config.categories.items():
            assert set(spec.tools) <= set(t.registry.names())
            assert cat in t.submission_models


def test_unknown_key_is_rejected():
    data = raw()
    data["categories"]["quote_request"]["fieldz"] = {}
    with pytest.raises(ValidationError):
        TenantConfig.model_validate(data)


def test_missing_other_category_is_rejected():
    data = raw()
    del data["categories"]["other"]
    with pytest.raises(ValidationError, match="other"):
        TenantConfig.model_validate(data)


def test_bad_rule_expression_is_rejected_at_load():
    data = raw()
    data["policy"]["force_handoff_if"][0]["expr"] = "__import__('os')"
    with pytest.raises(ValidationError, match="may be called"):
        TenantConfig.model_validate(data)


def test_record_columns_must_exist():
    data = raw()
    data["categories"]["invoice"]["record"]["columns"].append("not_a_field")
    with pytest.raises(ValidationError, match="not_a_field"):
        TenantConfig.model_validate(data)


def test_unknown_tool_in_category(tmp_path):
    src = TENANTS_DIR / "larkspur_freight"
    dst = tmp_path / "larkspur_freight"
    import shutil

    shutil.copytree(src, dst)
    data = copy.deepcopy(raw())
    data["categories"]["quote_request"]["tools"].append("teleport")
    (dst / "tenant.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(TenantConfigError, match="teleport"):
        load_tenant(dst, var_root=tmp_path)
