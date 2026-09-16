from hotstartups.util import domain_of, normalize_name, looks_like_company_site, clip


def test_domain_and_names():
    assert domain_of("https://www.Example.co.uk/about?x=1") == "example.co.uk"
    assert domain_of("example.com") == "example.com"
    assert domain_of(None) is None
    assert normalize_name("Kite & Ledger, Inc.") == normalize_name("kite ledger")
    assert normalize_name("Tessaline GmbH") == "tessaline"
    assert looks_like_company_site("https://tessaline.de")
    assert not looks_like_company_site("https://www.linkedin.com/company/tessaline")
    assert clip("a" * 10, 5) == "aaaa…"


def test_find_by_domain_then_name(ctx):
    cfg, src, store = ctx
    rec = store.new_startup("Kite & Ledger", "https://kiteledger.com")
    store.set_website(rec, "https://kiteledger.com")
    store.save_startup(rec)
    assert store.find(url="https://www.kiteledger.com/about")["id"] == rec["id"]
    assert store.find(name="Kite and Ledger") is None  # 'and' is not '&'; different key on purpose
    assert store.find(name="Kite & Ledger Inc")["id"] == rec["id"]
    store.add_alias(rec, "KiteLedger")
    store.save_startup(rec)
    assert store.find(name="kiteledger")["id"] == rec["id"]
