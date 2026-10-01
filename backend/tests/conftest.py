def pytest_addoption(parser):
    parser.addoption("--openai-integration", action="store_true", default=False,
                     help="Explicitly enable paid live OpenAI tests using synthetic fixture data")
    parser.addoption("--neo4j-integration", action="store_true", default=False,
                     help="Explicitly enable live tests against NEO4J_TEST_DATABASE only")
    parser.addoption("--pg-integration", action="store_true", default=False,
                     help="Explicitly enable live PostgreSQL tests that load the synthetic CSVs into CITI_PG_DSN")
