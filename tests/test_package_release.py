import evoagent


def test_installed_package_exposes_stable_version():
    assert evoagent.__version__ == "2.0.0"
