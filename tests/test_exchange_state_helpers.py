from decimal import Decimal

from autocrypto.execution import PaperExchange


def test_exchange_state_helpers_are_outside_app_module():
    import autocrypto.app as app_module
    from autocrypto.exchange_state import adapter_status_for_exchange, paper_capabilities

    assert not hasattr(app_module, "_paper_capabilities")

    capabilities = paper_capabilities()
    assert capabilities.to_dict()["exchange_id"] == "paper"

    status = adapter_status_for_exchange("paper", PaperExchange(), equity=Decimal("500"))
    payload = status.to_dict()
    assert payload["exchange_id"] == "paper"
    assert payload["balances"][0]["available"] == "500"
