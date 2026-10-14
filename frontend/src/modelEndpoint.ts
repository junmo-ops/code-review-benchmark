export function validModelEndpoint(value: string) {
  const endpoint = value.trim().replace(/\/+$/, '');
  try {
    const url = new URL(endpoint);
    return ['http:', 'https:'].includes(url.protocol) && Boolean(url.hostname) &&
      !(url.username || url.password || url.search || url.hash) &&
      !/[\s{}<>]/.test(endpoint) && !url.pathname.endsWith('/chat/completions');
  } catch {
    return false;
  }
}
export const modelEndpointRule = {
  validator: (_rule: unknown, value: string) =>
    !value || validModelEndpoint(value) ? Promise.resolve() :
      Promise.reject(new Error('请填写有效的 HTTP/HTTPS 接口根地址，不含密码、查询参数或 /chat/completions')),
};
