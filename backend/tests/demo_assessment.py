"""Explicit test-only consent; production imports none of this helper."""
from ff_agent.investment_profile import ProfileService, IDS, SURVEY_VERSION

def consent_test_profile(bank,user='demo'):
    return ProfileService(bank).submit(user,{key:3 for key in IDS},SURVEY_VERSION,0,
                                       'legacy-financial-fixture',True)
