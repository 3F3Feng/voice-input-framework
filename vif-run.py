"""Minimal VIF server start - lazy model imports"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Patch models/__init__.py to lazy-import
import types
models_mod = types.ModuleType('server.models')
models_mod.__file__ = '/tmp/voice-input-framework/server/models/__init__.py'

from server.models.base import BaseSTTEngine, STTEngineError
# Don't import heavy model modules yet - they'll be loaded on demand
AVAILABLE_MODELS = {}  # Will be populated lazily

models_mod.BaseSTTEngine = BaseSTTEngine
models_mod.STTEngineError = STTEngineError
models_mod.AVAILABLE_MODELS = {
    'qwen_asr': None,
    'qwen_asr_small': None,
    'whisper': None,
    'whisper-small': None,
}

sys.modules['server.models'] = models_mod

# Now start the API - the STT engine manager will handle lazy loading
os.environ['VIF_LOG_LEVEL'] = 'INFO'
from server.api import main
main()
