"""Lead enrichment: fixture sites only, hidden and AI-directed text removed, score in code, checked drafts."""

from __future__ import annotations

from pathlib import Path

import pytest

from relay.config import Settings
from relay.gateway.leads import check_draft, load_icp
from relay.gateway.schemas import CompanyProfile, DraftOutput, DraftRequest, LeadRequest
from relay.gateway.service import Gateway
from relay.gateway.sites import SiteFetcher, SiteRefusedError, normalize_domain, visible_text

ICP = load_icp(Path("configs/icp.yaml"))
FETCHER = SiteFetcher(Path("data/leads/sites"))


@pytest.mark.parametrize(("website", "domain"), [
    ("https://www.tidepool-coffee.example/about", "tidepool-coffee.example"),
    ("tidepool-coffee.example", "tidepool-coffee.example"),
    ("HTTPS://North-Loom.example", "north-loom.example"),
])
def test_domains_are_normalised(website: str, domain: str) -> None:
    assert normalize_domain(website) == domain


@pytest.mark.parametrize("website", ["https://google.com", "http://127.0.0.1/admin", "", "file:///etc/passwd",
                                     "https://evil.example.com.attacker.io"])
def test_anything_but_fixture_sites_is_refused(website: str) -> None:
    with pytest.raises(SiteRefusedError):
        normalize_domain(website)


def test_hidden_elements_are_dropped() -> None:
    text, hidden = visible_text('<p>Visible</p><p style="display:none">Secret</p><div hidden>Also</div>'
                                "<script>var x=1</script>")
    assert text == "Visible" and hidden == 2


def test_injection_site_text_is_removed_before_the_model_sees_it() -> None:
    site = FETCHER.fetch("https://bloom-box-flowers.example")
    assert site.removed_hidden >= 1 and site.removed_ai_directed >= 1
    assert "5,000 employees" not in site.text and "38 florists" in site.text


def test_missing_site_is_reported_not_raised() -> None:
    assert FETCHER.fetch("https://quantum-brands.example").pages == []


def test_icp_score_and_tiers() -> None:
    profile = CompanyProfile(industry="food_beverage", employees_band="11-50", country="IE", sells_online=True)
    score, parts = ICP.score(profile, 1500)
    assert score == 30 + 20 + 15 + 5 + 15 and parts["quantity"] == 15
    assert ICP.tier(score, 70, 40) == "hot"
    assert ICP.score(CompanyProfile(industry="technology", country="US"), None)[0] == 5 + 5 + 5


def test_competitors_and_non_buyers_are_disqualified() -> None:
    assert ICP.disqualified(CompanyProfile(industry="printing_packaging")) == "competitor"
    assert ICP.disqualified(CompanyProfile(industry="education", buyer=False)) == "not_a_buyer"
    assert ICP.disqualified(CompanyProfile(industry="pets")) is None


def lead(**extra: object) -> LeadRequest:
    return LeadRequest.model_validate({"name": "Sean Murphy", "email": "sean@tidepool-coffee.example",
                                       "company": "Tidepool Coffee", "website": "tidepool-coffee.example",
                                       "message": "1,500 mailer boxes", "product": "mailer_boxes",
                                       "quantity": 1500, **extra})


def test_enrichment_with_the_rules_model(gateway: Gateway) -> None:
    result = gateway.enrich_lead(lead())
    assert result.site.found and result.profile.industry == "food_beverage"
    assert result.profile.country == "IE" and result.tier == "hot" and result.decision == "draft_reply"


def test_lead_without_a_site_goes_to_research(gateway: Gateway) -> None:
    result = gateway.enrich_lead(lead(website="https://quantum-brands.example"))
    assert result.decision == "review" and not result.site.found and result.meta.model == ""


def test_lead_with_an_outside_website_is_not_fetched(gateway: Gateway) -> None:
    result = gateway.enrich_lead(lead(website="https://www.real-company.com"))
    assert result.decision == "review" and "not a fixture site" in (result.site.error or "")


def draft(body: str, subject: str = "Your enquiry") -> DraftOutput:
    return DraftOutput(subject=subject, body=body)


GOOD = ("Hi Sean, thank you for your enquiry about 1,500 mailer boxes for Tidepool Coffee. Production takes 10 "
        "business days after proof approval and volume discounts start at 1,000 units, so your order qualifies "
        "for 5% off. We can send a free sample pack first. Would a short call this week work? "
        "The Kestrel Print Co. sales team")


def test_good_draft_passes_every_check() -> None:
    result = check_draft(draft(GOOD), DraftRequest(lead=lead()))
    assert result.ok, result.checks


def test_draft_with_invented_numbers_or_placeholders_fails() -> None:
    invented = check_draft(draft(GOOD.replace("5% off", "40% off")), DraftRequest(lead=lead()))
    assert not invented.ok and any(c.code == "numbers_grounded" and not c.ok for c in invented.checks)
    placeholder = check_draft(draft(GOOD + " [Your Name]"), DraftRequest(lead=lead()))
    assert any(c.code == "no_placeholders" and not c.ok for c in placeholder.checks)
    anonymous = check_draft(draft(GOOD.replace("Hi Sean", "Hello")), DraftRequest(lead=lead()))
    assert any(c.code == "greets_by_name" and not c.ok for c in anonymous.checks)


def test_rules_model_draft_passes_its_own_checks(gateway: Gateway) -> None:
    result = gateway.draft_reply(DraftRequest(lead=lead(), profile=CompanyProfile(industry="food_beverage")))
    assert result.ok, [c for c in result.checks if not c.ok]


def test_settings_tiers_are_configurable() -> None:
    assert Settings(llm_ledger=None, lead_hot_score=90).lead_hot_score == 90
