"""Use the dataset repository identifier verbatim; never translate aliases."""
import re


def validate(config):
    if 'repositories' in config:
        raise ValueError('已取消仓库名称映射，请删除 repositories，并使用填写行内仓库名的新案例集')


def resolve(config, repository):
    validate(config)
    if not isinstance(repository, str) or not repository.strip():
        raise ValueError('案例仓库标识不能为空')
    if config.get('contract') == 'company-repo-review-v1':
        if not re.fullmatch(r'S[0-9]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('请使用新案例集，仓库名须为完整行内标识，例如 S990768/LV01.30_Checkmate；不再转换开源仓库名')
    return repository
