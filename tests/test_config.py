from src.config import load_settings, check_environment, env_names
from src.errors import ConfigError

def test_audiobook_defaults():
    s=load_settings({})
    assert s.audiobook.target_minutes==60
    assert s.audiobook.wpm==130
    assert s.audiobook.persons_dir=='persons'

def test_no_finance_section():
    s=load_settings({})
    assert not hasattr(s,'finance')

def test_typo_protection():
    try: load_settings({'AUDIOBOOK_TARET_MINUTES':'30'})
    except ConfigError as e: assert 'did you mean' in str(e)
    else: raise AssertionError('expected ConfigError')

def test_known_env_names():
    names=env_names(); assert 'AUDIOBOOK_TARGET_MINUTES' in names; assert 'YOUTUBE_TOKEN_JSON_BOOKS' in names
    assert 'FINANCE_TARGET_MINUTES' not in names
