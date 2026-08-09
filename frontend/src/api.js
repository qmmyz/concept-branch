export async function api(path, options = {}) {
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData
  const response = await fetch(path, {
    headers: { ...(!isFormData ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {}
    if (response.status === 401 && !path.startsWith('/api/auth/')) {
      window.dispatchEvent(new CustomEvent('auth:expired'))
      detail = '未登录或登录已过期'
    }
    throw new Error(detail)
  }
  if (response.status === 204) return null
  return response.json()
}
