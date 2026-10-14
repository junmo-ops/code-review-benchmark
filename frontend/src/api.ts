let csrf = '';
let authorization = '';
let session: Promise<void> | undefined;
export class AuthenticationRequired extends Error {}
export class ApiError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}
export function setAuthentication(username: string, password: string) {
  const bytes = new TextEncoder().encode(username + ':' + password);
  authorization = 'Basic ' + btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(''));
  csrf = '';
}
function requestHeaders(): Record<string, string> {
  return { 'X-Dashboard-Token': csrf, ...(authorization ? { Authorization: authorization } : {}) };
}
export async function bootstrap() {
  if (csrf) return;
  if (!session)
    session = api<{ csrf_token: string }>('/api/session')
      .then((value) => {
        if (!value.csrf_token) throw new Error('API 未提供有效页面令牌');
        csrf = value.csrf_token;
      })
      .finally(() => {
        session = undefined;
      });
  await session;
}
async function parse<T>(response: Response): Promise<T> {
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`HTTP ${response.status}：服务器返回了非 JSON 响应，请检查应用与平台日志`);
  }
  if (response.status === 401) {
    csrf = '';
    throw new AuthenticationRequired(body.error ?? '请登录测评工作台');
  }
  if (response.status === 403 && body.error === '页面会话失效，请刷新') csrf = '';
  if (!response.ok) throw new ApiError(response.status, body.error ?? `操作失败（HTTP ${response.status}）`);
  return body;
}
export async function api<T>(
  path: string,
  data?: unknown,
  method?: string,
  signal?: AbortSignal,
): Promise<T> {
  const headers = requestHeaders();
  if (data !== undefined) headers['Content-Type'] = 'application/json';
  return parse<T>(
    await fetch(path, {
      headers,
      method: method ?? (data === undefined ? 'GET' : 'POST'),
      body: data === undefined ? undefined : JSON.stringify(data),
      signal,
    }),
  );
}
export function query(path: string, values: Record<string, string>) {
  return path + '?' + new URLSearchParams(values);
}
export function upload<T>(
  path: string,
  file: File,
  progress: (percent: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', path);
    for (const [key, value] of Object.entries(requestHeaders()))
      request.setRequestHeader(key, value);
    request.setRequestHeader('Content-Type', 'application/zip');
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) progress(Math.floor((event.loaded / event.total) * 100));
    };
    request.upload.onload = () => progress(100);
    request.onload = () => {
      try {
        const body = JSON.parse(request.responseText);
        if (request.status < 200 || request.status >= 300)
          throw new Error(body.error ?? `上传失败（HTTP ${request.status}）`);
        resolve(body);
      } catch (error) {
        reject(
          error instanceof SyntaxError
            ? new Error(`HTTP ${request.status}：上传响应不是 JSON，请检查平台日志`)
            : error,
        );
      }
    };
    request.onerror = () => reject(new Error('上传连接中断，请检查网络和页面服务'));
    request.onabort = () => reject(new Error('上传已中断'));
    request.send(file);
  });
}
export async function uploadFingerprint(file: File): Promise<string> {
  const chunk = 8 * 1024 * 1024;
  const first = new Uint8Array(await crypto.subtle.digest('SHA-256', await file.slice(0, chunk).arrayBuffer()));
  const last = new Uint8Array(await crypto.subtle.digest('SHA-256', await file.slice(Math.max(0, file.size - chunk)).arrayBuffer()));
  const hex = (value: Uint8Array) => Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
  const identity = new TextEncoder().encode(`${file.name}\0${file.size}\0${hex(first)}\0${hex(last)}`);
  const result = new Uint8Array(await crypto.subtle.digest('SHA-256', identity));
  return hex(result);
}
export function uploadChunk(
  id: string,
  index: number,
  chunk: Blob,
  progress: (sent: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', `/api/uploads/${id}/chunks/${index}`);
    for (const [key, value] of Object.entries(requestHeaders())) request.setRequestHeader(key, value);
    request.setRequestHeader('Content-Type', 'application/octet-stream');
    request.upload.onprogress = (event) => progress(event.loaded);
    request.onload = () => {
      try {
        const body = JSON.parse(request.responseText);
        if (request.status < 200 || request.status >= 300)
          throw new Error(body.error ?? `上传分块失败（HTTP ${request.status}）`);
        resolve();
      } catch (error) {
        reject(error instanceof SyntaxError ? new Error(`上传分块响应无效（HTTP ${request.status}）`) : error);
      }
    };
    request.onerror = () => reject(new Error('上传连接中断；已完成分块已保存，可重新选择同一 ZIP 续传'));
    request.onabort = () => reject(new Error('上传已中断；已完成分块已保存，可重新选择同一 ZIP 续传'));
    request.send(chunk);
  });
}
export function download(name: string, content: string | Blob) {
  const url = URL.createObjectURL(
    content instanceof Blob ? content : new Blob([content], { type: 'text/plain;charset=utf-8' }),
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function downloadExport(key: string) {
  const response = await fetch(query('/api/export', { key }), {
    headers: requestHeaders(),
  });
  if (!response.ok) {
    await parse(response);
    return;
  }
  download(key.split('/').pop() + '-results.tar.gz', await response.blob());
}
