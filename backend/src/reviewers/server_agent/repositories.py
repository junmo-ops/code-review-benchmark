"""Resolve optional repository aliases consistently for requests and evidence."""


def validate(config):
    aliases = config.get('repositories', {})
    if not isinstance(aliases, dict):
        raise ValueError('仓库映射必须是对象，可省略或留空')
    for key, value in aliases.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key.strip() or not value.strip():
            raise ValueError('仓库映射必须为非空字符串')


def resolve(config, repository):
    value = config.get('repositories', {}).get(repository, repository)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('案例仓库标识不能为空')
    return value
