"""One bounded, non-promoting live model check before expensive L3 batches."""
import json
import time
from .research_providers import GoModel, ResearchConfig, ProviderError


def main():
    config = ResearchConfig.from_env()
    if config.problem():
        print(json.dumps({'status': 'configuration_error', 'error': config.problem()}))
        return 2
    start = time.monotonic()
    try:
        result, tokens = GoModel(config).complete(
            {'stage': 'plan', 'task': {'jurisdiction': '高雄市',
             'questions': ['高雄市近期地方治理議題有哪些官方更新？']}, 'max_queries': 1},
            'l3-preflight', 90)
        if not isinstance(result.get('queries'), list) or not result['queries']:
            raise ProviderError('model_empty_plan', retryable=False)
        print(json.dumps({'status': 'ok', 'elapsed_seconds': round(time.monotonic()-start, 1),
                          'tokens': tokens, 'query_count': len(result['queries'])}))
        return 0
    except ProviderError as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc),
                          'retry_after_seconds': exc.retry_after,
                          'elapsed_seconds': round(time.monotonic()-start, 1)}))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
