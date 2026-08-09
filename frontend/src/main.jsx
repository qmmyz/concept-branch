import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import './styles.css'
import { api } from './api'

const EMPTY_SETTINGS = { base_url: '', protocol: 'chat_completions', model: '', has_api_key: false }

function Markdown({ children }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
      {children}
    </ReactMarkdown>
  )
}

function TreeNode({ node, nodes, currentId, onSelect }) {
  const children = nodes.filter((item) => item.parent_id === node.id)
  return (
    <li>
      <button className={`tree-node ${currentId === node.id ? 'active' : ''}`} onClick={() => onSelect(node.id)}>
        <span>{node.parent_id ? '↳' : '◆'}</span>{node.title}
      </button>
      {children.length > 0 && <ul>{children.map((child) => <TreeNode key={child.id} node={child} nodes={nodes} currentId={currentId} onSelect={onSelect} />)}</ul>}
    </li>
  )
}

function ProviderSettings({ onClose, onChanged }) {
  const [providers, setProviders] = useState([])
  const [active, setActive] = useState({})
  const [form, setForm] = useState({ name: '新的中转站', base_url: '', protocol: 'chat_completions', model: '', models: '', kind: 'chat' })
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [editingProviderId, setEditingProviderId] = useState(null)
  const [editingName, setEditingName] = useState('')
  const [renameBusy, setRenameBusy] = useState(false)
  const keyInput = useRef(null)

  useEffect(() => {
    api('/api/providers').then((data) => { setProviders(data.providers); setActive(data.active) }).catch((error) => setStatus(error.message))
  }, [])

  const submit = async () => {
    setBusy(true); setStatus('')
    try {
      const payload = { ...form, model: form.model.trim(), models: form.models.split(',').map((item) => item.trim()).filter(Boolean), api_key: keyInput.current?.value || '' }
      const data = await api('/api/providers', { method: 'POST', body: JSON.stringify(payload) })
      if (keyInput.current) keyInput.current.value = ''
      setStatus('连接成功，Provider 已安全保存')
      setProviders((items) => [...items, data.provider]); setActive(data.active); onChanged()
    } catch (error) { setStatus(error.message) } finally { setBusy(false) }
  }

  const choose = async (provider, model) => {
    try { const selected = await api('/api/active-model', { method: 'PUT', body: JSON.stringify({ provider_id: provider.id, model }) }); setActive({ ...selected, provider_name: provider.name }); onChanged() } catch (error) { setStatus(error.message) }
  }
  const beginRename = (provider) => {
    setEditingProviderId(provider.id)
    setEditingName(provider.name)
    setStatus('')
  }
  const cancelRename = () => {
    setEditingProviderId(null)
    setEditingName('')
  }
  const saveRename = async (provider) => {
    const name = editingName.trim()
    if (!name) { setStatus('名称不能为空'); return }
    setRenameBusy(true); setStatus('')
    try {
      const updated = await api(`/api/providers/${provider.id}`, { method: 'PATCH', body: JSON.stringify({ name }) })
      setProviders((items) => items.map((item) => item.id === provider.id ? { ...item, ...updated } : item))
      setActive((current) => current.provider_id === provider.id ? { ...current, provider_name: name } : current)
      cancelRename()
      onChanged()
      setStatus('Provider 名称已更新')
    } catch (error) { setStatus(error.message) } finally { setRenameBusy(false) }
  }
  const discover = async (provider) => {
    try { const data = await api(`/api/providers/${provider.id}/discover-models`, { method: 'POST' }); setProviders((items) => items.map((item) => item.id === provider.id ? { ...item, models: data.models } : item)); setStatus(`${provider.name} 已发现 ${data.models.length} 个模型`) } catch (error) { setStatus(error.message) }
  }
  const remove = async (provider) => {
    if (!window.confirm(`删除 Provider“${provider.name}”？`)) return
    try { await api(`/api/providers/${provider.id}`, { method: 'DELETE' }); const data = await api('/api/providers'); setProviders(data.providers); setActive(data.active); onChanged() } catch (error) { setStatus(error.message) }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-label="模型设置">
      <form className="modal provider-modal" onSubmit={(event) => { event.preventDefault(); submit() }}>
        <div className="modal-head"><h2>模型与中转站</h2><button type="button" className="icon" onClick={onClose}>×</button></div>
        <div className="provider-list">{providers.length === 0 && <p className="hint">还没有 Provider，请在下方添加。</p>}{providers.map((provider) => <div className="provider-row" key={provider.id}><div className="provider-main">{editingProviderId === provider.id ? <div className="provider-name-edit"><input aria-label={`${provider.name} 名称`} value={editingName} onChange={(e) => setEditingName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); saveRename(provider) } if (e.key === 'Escape') cancelRename() }} autoFocus /><button type="button" onClick={() => saveRename(provider)} disabled={renameBusy}>保存</button><button type="button" className="secondary" onClick={cancelRename} disabled={renameBusy}>取消</button></div> : <div className="provider-name-row"><strong>{provider.name}</strong><button type="button" className="icon" aria-label={`重命名 ${provider.name}`} onClick={() => beginRename(provider)}>✎</button></div>}<small>{provider.base_url} · {provider.has_api_key ? '已配置 key' : '未配置 key'}</small><select aria-label={`${provider.name} 模型`} value={active.provider_id === provider.id ? active.model : (provider.models[0] || '')} onChange={(e) => choose(provider, e.target.value)}>{provider.models.map((model) => <option key={model}>{model}</option>)}</select></div><div className="provider-actions"><button type="button" className="secondary" onClick={() => discover(provider)}>发现模型</button><button type="button" className="icon" aria-label={`删除 ${provider.name}`} onClick={() => remove(provider)}>×</button></div></div>)}</div>
        <h3>添加 Provider</h3>
        <label>名称<input aria-label="Provider 名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
        <label>API base URL<input aria-label="API base URL" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="https://example.com/v1" required /></label>
        <label>API 协议<select aria-label="API 协议" value={form.protocol} onChange={(e) => setForm({ ...form, protocol: e.target.value })}><option value="chat_completions">Chat Completions</option><option value="responses">Responses API</option></select></label>
        <label>用途<select aria-label="Provider 用途" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}><option value="chat">普通聊天</option><option value="design">设计 worker（不用于普通聊天）</option></select></label>
        <label>默认模型<input aria-label="模型" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="可留空，保存时自动发现" /></label>
        <label>其他模型（逗号分隔）<input aria-label="其他模型" value={form.models} onChange={(e) => setForm({ ...form, models: e.target.value })} placeholder="model-a, model-b" /></label>
        <label>API key<input ref={keyInput} aria-label="API key" type="password" autoComplete="new-password" defaultValue="" placeholder="仅发送给本机后端" /></label>
        <p className="hint">key 只用于本次连接测试和本机受限保存，不进入浏览器持久化状态或 API 响应。</p>
        {status && <p className="status" role="status">{status}</p>}
        <div className="modal-actions"><button disabled={busy}>测试并保存 Provider</button></div>
      </form>
    </div>
  )
}

function ProviderSwitcher({ data, onChanged, onOpenSettings }) {
  const activeProvider = data.providers.find((provider) => provider.id === data.active.provider_id)
  if (!activeProvider) return <button className="secondary switcher" onClick={onOpenSettings}>设置模型</button>
  return <div className="switcher"><select aria-label="活动 Provider" value={activeProvider.id} onChange={(e) => { const provider = data.providers.find((item) => item.id === e.target.value); if (provider?.models[0]) api('/api/active-model', { method: 'PUT', body: JSON.stringify({ provider_id: provider.id, model: provider.models[0] }) }).then(onChanged) }}><option value={activeProvider.id}>{activeProvider.name}</option>{data.providers.filter((provider) => provider.id !== activeProvider.id).map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select><select aria-label="活动模型" value={data.active.model || ''} onChange={(e) => api('/api/active-model', { method: 'PUT', body: JSON.stringify({ provider_id: activeProvider.id, model: e.target.value }) }).then(onChanged)}>{activeProvider.models.map((model) => <option key={model}>{model}</option>)}</select></div>
}

function ResizeHandle({ side, onResize }) {
  const [dragging, setDragging] = useState(false)
  return <div className={`resize-handle ${side} ${dragging ? 'dragging' : ''}`} role="separator" aria-orientation="vertical" aria-label={`${side === 'left' ? '会话栏' : '分支栏'}宽度`} onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); setDragging(true) }} onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) onResize(event.clientX) }} onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); setDragging(false) }} onPointerCancel={() => setDragging(false)} />
}

function initialTheme() {
  try {
    const saved = window.localStorage.getItem('concept-branch-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch {}
  return 'dark'
}

function searchKindLabel(kind) {
  return kind === 'discussion' ? '讨论' : kind === 'node' ? '分支' : '消息'
}

function AuthScreen({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true); setStatus('')
    try {
      await api(mode === 'login' ? '/api/auth/login' : '/api/auth/register', { method: 'POST', body: JSON.stringify({ username, password }) })
      onAuthed()
    } catch (error) { setStatus(error.message) } finally { setBusy(false) }
  }

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand"><div className="brand-mark">CB</div><h1>Concept Branch</h1><p>登录后进入你的想法分支空间</p></div>
        {mode === 'register' && <p className="hint">首个注册用户会标记为管理员；v0.1 暂无管理员专属权限</p>}
        <label>用户名<input aria-label="用户名" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required /></label>
        <label>密码<input aria-label="密码" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required /></label>
        {status && <p className="status" role="status">{status}</p>}
        <button disabled={busy}>{mode === 'login' ? '登录' : '注册'}</button>
        <p className="auth-switch">{mode === 'login' ? '还没有账号？' : '已有账号？'}<button type="button" className="secondary" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setStatus('') }}>{mode === 'login' ? '注册一个' : '去登录'}</button></p>
      </form>
    </div>
  )
}

function App() {
  const [user, setUser] = useState(null)
  const [authBusy, setAuthBusy] = useState(true)
  const [discussions, setDiscussions] = useState([])
  const [discussionId, setDiscussionId] = useState(null)
  const [nodes, setNodes] = useState([])
  const [nodeId, setNodeId] = useState(null)
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [providerData, setProviderData] = useState({ providers: [], active: {} })
  const [selection, setSelection] = useState(null)
  const [customQuestion, setCustomQuestion] = useState('')
  const [theme, setTheme] = useState(initialTheme)
  const [leftWidth, setLeftWidth] = useState(() => Number(window.localStorage.getItem('concept-branch-left-width')) || 280)
  const [rightWidth, setRightWidth] = useState(() => Number(window.localStorage.getItem('concept-branch-right-width')) || 256)
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [attachments, setAttachments] = useState([])
  const [uploadBusy, setUploadBusy] = useState(false)
  const shellRef = useRef(null)
  const focusMessageRef = useRef(null)
  const messagesRequestRef = useRef(0)
  const fileInputRef = useRef(null)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { window.localStorage.setItem('concept-branch-theme', theme) } catch {}
  }, [theme])
  useEffect(() => { try { window.localStorage.setItem('concept-branch-left-width', String(leftWidth)) } catch {} }, [leftWidth])
  useEffect(() => { try { window.localStorage.setItem('concept-branch-right-width', String(rightWidth)) } catch {} }, [rightWidth])
  const resizeLeft = (clientX) => {
    const bounds = shellRef.current?.getBoundingClientRect()
    if (bounds) setLeftWidth(Math.min(420, Math.max(240, clientX - bounds.left - 10)))
  }
  const resizeRight = (clientX) => {
    const bounds = shellRef.current?.getBoundingClientRect()
    if (bounds) setRightWidth(Math.min(400, Math.max(200, bounds.right - clientX - 10)))
  }

  const currentNode = nodes.find((item) => item.id === nodeId)
  const roots = nodes.filter((item) => item.parent_id === null)
  const breadcrumbs = useMemo(() => {
    const result = []
    let current = currentNode
    while (current) { result.unshift(current); current = nodes.find((item) => item.id === current.parent_id) }
    return result
  }, [currentNode, nodes])

  useEffect(() => {
    api('/api/auth/me').then((data) => setUser(data.user)).catch(() => setUser(undefined)).finally(() => setAuthBusy(false))
  }, [])

  useEffect(() => {
    const onExpired = () => { setUser(undefined); setDiscussions([]); setDiscussionId(null); setNodes([]); setNodeId(null); setMessages([]); setSettingsOpen(false); setError('') }
    window.addEventListener('auth:expired', onExpired)
    return () => window.removeEventListener('auth:expired', onExpired)
  }, [])

  const logout = async () => {
    try { await api('/api/auth/logout', { method: 'POST' }) } catch {}
    setUser(undefined); setDiscussions([]); setDiscussionId(null); setNodes([]); setNodeId(null); setMessages([]); setSettingsOpen(false); setError('')
  }

  const onAuthed = () => {
    api('/api/auth/me').then((data) => setUser(data.user)).catch(() => setUser(undefined))
  }

  const refreshDiscussions = async (preferred) => {
    const items = await api('/api/discussions')
    setDiscussions(items)
    const id = preferred || discussionId || items[0]?.id || null
    setDiscussionId(id)
  }

  useEffect(() => { if (user) refreshDiscussions().catch((e) => setError(e.message)) }, [user])
  const refreshProviders = () => api('/api/providers').then(setProviderData).catch((e) => setError(e.message))
  useEffect(() => { if (user) refreshProviders() }, [user])
  useEffect(() => {
    const query = searchQuery.trim()
    if (!user || !query) { setSearchResults([]); setSearching(false); return undefined }
    const controller = new AbortController()
    setSearching(true)
    api(`/api/search?q=${encodeURIComponent(query)}`, { signal: controller.signal }).then((data) => setSearchResults(data.results)).catch((error) => {
      if (error.name !== 'AbortError') setError(error.message)
    }).finally(() => { if (!controller.signal.aborted) setSearching(false) })
    return () => controller.abort()
  }, [searchQuery, user])
  useEffect(() => {
    if (!discussionId) { setNodes([]); setNodeId(null); return }
    api(`/api/discussions/${discussionId}/nodes`).then((items) => {
      setNodes(items)
      setNodeId((existing) => items.some((item) => item.id === existing) ? existing : items.find((item) => item.parent_id === null)?.id)
    }).catch((e) => setError(e.message))
  }, [discussionId])
  useEffect(() => {
    const requestId = ++messagesRequestRef.current
    if (!nodeId) { setMessages([]); setAttachments([]); return }
    setMessages((items) => items.every((item) => item.node_id === nodeId) ? items : [])
    api(`/api/nodes/${nodeId}/messages`).then((items) => {
      if (requestId !== messagesRequestRef.current) return
      setMessages(items)
      if (focusMessageRef.current) {
        const messageId = focusMessageRef.current
        focusMessageRef.current = null
        window.setTimeout(() => document.querySelector(`[data-message-id="${messageId}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 0)
      }
    }).catch((e) => setError(e.message))
    api(`/api/nodes/${nodeId}/attachments`).then((data) => {
      if (requestId === messagesRequestRef.current) setAttachments(data.attachments)
    }).catch((e) => setError(e.message))
  }, [nodeId])

  const createDiscussion = async () => {
    const title = window.prompt('讨论名称', '新的想法')
    if (!title?.trim()) return
    const result = await api('/api/discussions', { method: 'POST', body: JSON.stringify({ title }) })
    await refreshDiscussions(result.discussion.id)
  }

  const renameDiscussion = async (item, event) => {
    event.stopPropagation()
    const title = window.prompt('重命名讨论', item.title)
    if (!title?.trim()) return
    await api(`/api/discussions/${item.id}`, { method: 'PATCH', body: JSON.stringify({ title }) })
    await refreshDiscussions(item.id)
  }

  const removeDiscussion = async (item, event) => {
    event.stopPropagation()
    if (!window.confirm(`删除讨论“${item.title}”？此操作会删除其中全部卡片。`)) return
    await api(`/api/discussions/${item.id}`, { method: 'DELETE' })
    setDiscussionId(null); setNodeId(null)
    await refreshDiscussions()
  }

  const openSearchResult = (result) => {
    setSearchQuery('')
    setSearchResults([])
    focusMessageRef.current = result.kind === 'message' ? result.message_id : null
    setDiscussionId(result.discussion_id)
    setNodeId(result.node_id)
  }

  const send = async (event) => {
    event?.preventDefault()
    if (!draft.trim() || !nodeId || busy) return
    const content = draft.trim()
    const pendingId = `pending-${Date.now()}-${Math.random().toString(16).slice(2)}`
    const pendingMessage = { id: pendingId, node_id: nodeId, role: 'user', content, pending: true }
    messagesRequestRef.current += 1
    setMessages((items) => [...items, pendingMessage])
    setDraft('')
    setBusy(true); setError('')
    try {
      const result = await api(`/api/nodes/${nodeId}/messages`, { method: 'POST', body: JSON.stringify({ content }) })
      setMessages((items) => items.flatMap((item) => item.id === pendingId ? [result.user, result.assistant] : [item]))
      await refreshDiscussions(discussionId)
    } catch (e) {
      setMessages((items) => items.map((item) => item.id === pendingId ? { ...item, pending: false, failed: true } : item))
      setDraft((current) => current || content)
      setError(e.message)
    } finally { setBusy(false) }
  }

  const handleComposerKeyDown = (event) => {
    if (event.key !== 'Enter' || event.shiftKey) return
    if (event.nativeEvent?.isComposing || event.isComposing) return
    event.preventDefault()
    event.currentTarget.form?.requestSubmit()
  }

  const uploadAttachment = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || !nodeId || uploadBusy) return
    const form = new FormData()
    form.append('file', file)
    setUploadBusy(true); setError('')
    try {
      await api(`/api/nodes/${nodeId}/attachments`, { method: 'POST', body: form })
      const data = await api(`/api/nodes/${nodeId}/attachments`)
      setAttachments(data.attachments)
    } catch (e) { setError(e.message) } finally { setUploadBusy(false) }
  }

  const removeAttachment = async (attachment) => {
    if (!nodeId || attachment.inherited || !window.confirm(`删除文件“${attachment.filename}”？`)) return
    try {
      await api(`/api/nodes/${nodeId}/attachments/${attachment.id}`, { method: 'DELETE' })
      setAttachments((items) => items.filter((item) => item.id !== attachment.id))
    } catch (e) { setError(e.message) }
  }

  const formatBytes = (bytes) => bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`

  const captureSelection = (messageId, role) => {
    const selected = window.getSelection()
    const text = selected?.toString().trim()
    if (!text || selected.rangeCount === 0) return
    const rect = selected.getRangeAt(0).getBoundingClientRect()
    setSelection({ messageId, role, text, x: Math.min(rect.left, window.innerWidth - 340), y: rect.bottom + 8, custom: false })
  }

  const expand = async (question = null) => {
    if (!selection || busy) return
    setBusy(true); setError('')
    try {
      const result = await api(`/api/nodes/${nodeId}/expand`, { method: 'POST', body: JSON.stringify({ source_message_id: selection.messageId, selected_text: selection.text, custom_question: question }) })
      setNodes((items) => [...items, result.node]); setSelection(null); setCustomQuestion(''); setNodeId(result.node.id); setMessages(result.messages)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (authBusy) return <div className="auth-screen"><div className="auth-card"><p className="status">加载中…</p></div></div>
  if (!user) return <AuthScreen onAuthed={onAuthed} />

  return (
    <div ref={shellRef} className="app-shell" style={{ '--left-width': leftCollapsed ? '0px' : `${leftWidth}px`, '--right-width': rightCollapsed ? '0px' : `${rightWidth}px` }}>
      <aside className={`sidebar discussions ${leftCollapsed ? 'is-collapsed' : ''}`}>
        <div className="brand"><div className="brand-mark">CB</div><div><strong>Concept Branch</strong><small>想法的分支空间</small></div></div>
        <button className="new-discussion" onClick={createDiscussion}>＋ 新建讨论</button>
        <div className="search-area"><div className="search-box"><span aria-hidden="true">⌕</span><input aria-label="搜索讨论和消息" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="搜索讨论和消息" />{searchQuery && <button type="button" className="search-clear" aria-label="清除搜索" onClick={() => setSearchQuery('')}>×</button>}</div>{searchQuery.trim() && <div className="search-results" aria-live="polite">{searching && <p className="search-empty">搜索中…</p>}{!searching && searchResults.length === 0 && <p className="search-empty">没有找到匹配内容</p>}{!searching && searchResults.map((result) => <button key={`${result.kind}-${result.message_id || result.node_id || result.discussion_id}`} className="search-result" onClick={() => openSearchResult(result)}><span className="search-result-kind">{searchKindLabel(result.kind)}</span><strong>{result.discussion_title}</strong><small>{result.node_title}{result.kind === 'message' ? ` · ${result.role === 'assistant' ? 'AI' : '你'}` : ''}</small><span>{result.snippet}</span></button>)}</div>}</div>
        {!searchQuery.trim() && <div className="discussion-list">{discussions.map((item) => <button key={item.id} className={`discussion-item ${discussionId === item.id ? 'active' : ''}`} onClick={() => setDiscussionId(item.id)}><span>{item.title}</span><span className="item-actions"><i onClick={(e) => renameDiscussion(item, e)}>✎</i><i onClick={(e) => removeDiscussion(item, e)}>×</i></span></button>)}</div>}
        <div className="provider-dock"><span className="provider-dock-label">当前模型</span><ProviderSwitcher data={providerData} onChanged={refreshProviders} onOpenSettings={() => setSettingsOpen(true)} /></div>
        <button className="settings-button" onClick={() => setSettingsOpen(true)}>⚙ 模型与中转站</button>
        <button className="theme-toggle" onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')} aria-label={theme === 'dark' ? '切换浅色模式' : '切换深色模式'}><span aria-hidden="true">{theme === 'dark' ? '☀' : '◐'}</span><span>{theme === 'dark' ? '浅色模式' : '深色模式'}</span></button>
        <div className="auth-bar"><span className="auth-user">👤 {user.username}</span><button className="secondary" onClick={logout}>登出</button></div>
      </aside>

      <main className="conversation">
        <header>
          <nav className="breadcrumbs" aria-label="卡片路径">{breadcrumbs.map((item, index) => <React.Fragment key={item.id}><button onClick={() => setNodeId(item.id)}>{item.title}</button>{index < breadcrumbs.length - 1 && <span>›</span>}</React.Fragment>)}</nav>
          <div className="header-actions">{currentNode?.parent_id && <button className="secondary parent-button" onClick={() => setNodeId(currentNode.parent_id)}>← 返回父卡片</button>}<div className="workspace-controls"><button className="toolbar-toggle" onClick={() => setLeftCollapsed((value) => !value)} aria-label={leftCollapsed ? '显示会话栏' : '收起会话栏'} title={leftCollapsed ? '显示会话栏' : '收起会话栏'}>{leftCollapsed ? '›' : '‹'}</button><button className="toolbar-toggle" onClick={() => setRightCollapsed((value) => !value)} aria-label={rightCollapsed ? '显示分支栏' : '收起分支栏'} title={rightCollapsed ? '显示分支栏' : '收起分支栏'}>{rightCollapsed ? '‹' : '›'}</button></div></div>
        </header>
        <section className="messages" data-testid="messages">
          {!discussionId && <div className="empty"><h1>把一个想法，沿着概念继续展开</h1><p>创建讨论后，与 AI 对话；选中回答中的文字即可建立不打断主线的子卡片。</p><button onClick={createDiscussion}>创建第一个讨论</button></div>}
          {discussionId && messages.length === 0 && <div className="empty compact"><h1>{currentNode?.title}</h1><p>在下方输入问题，开始这张卡片的独立对话。</p></div>}
          {messages.map((message) => <article key={message.id} className={`message ${message.role} ${message.pending ? 'pending' : ''} ${message.failed ? 'failed' : ''}`} data-message-id={message.id}><div className="bubble" onMouseUp={() => !message.pending && !message.failed && captureSelection(message.id, message.role)}>{message.role === 'assistant' ? <Markdown>{message.content}</Markdown> : <p>{message.content}</p>}</div>{message.pending && <small className="message-state" role="status">已发送，正在等待回答…</small>}{message.failed && <small className="message-state failed">发送失败，内容已恢复到输入框</small>}</article>)}
          {busy && <div className="thinking">模型正在生成回答…</div>}
        </section>
        {discussionId && <div className="composer-area">
          {attachments.length > 0 && <div className="attachment-list" aria-label="背景文件">{attachments.map((attachment) => <div className="attachment-chip" key={attachment.id}><span className="attachment-format">{attachment.format.toUpperCase()}</span><span className="attachment-name" title={attachment.filename}>{attachment.filename}</span><small>{formatBytes(attachment.size_bytes)}{attachment.inherited ? ' · 继承' : ''}{attachment.truncated ? ' · 已截断' : ''}</small>{!attachment.inherited && <button type="button" className="attachment-remove" aria-label={`删除文件 ${attachment.filename}`} onClick={() => removeAttachment(attachment)}>×</button>}</div>)}</div>}
          <form className="composer" onSubmit={send}><input ref={fileInputRef} className="file-input" type="file" accept=".pdf,.txt,.md,.markdown,.csv,.json,.docx" onChange={uploadAttachment} /><button type="button" className="attach-button secondary" onClick={() => fileInputRef.current?.click()} disabled={uploadBusy || busy} aria-label="上传背景文件">{uploadBusy ? '上传中…' : '＋ 文件'}</button><textarea aria-label="消息" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={attachments.length ? '基于背景文件继续提问…' : '继续这张卡片的讨论…'} onKeyDown={handleComposerKeyDown} /><button disabled={busy || !draft.trim()}>发送</button></form>
        </div>}
        {error && <div className="error" role="alert">{error}<button onClick={() => setError('')}>×</button></div>}
      </main>

      <aside className={`sidebar tree-panel ${rightCollapsed ? 'is-collapsed' : ''}`}>
        <div className="panel-title"><span>卡片树</span><small>{nodes.length} 张</small></div>
        <ul className="tree">{roots.map((root) => <TreeNode key={root.id} node={root} nodes={nodes} currentId={nodeId} onSelect={setNodeId} />)}</ul>
        <div className="tree-tip"><strong>如何展开？</strong><p>在你提供的背景材料或 AI 回答中选中文字，选择“直接解释”或提出自定义问题。</p></div>
      </aside>

      {!leftCollapsed && <ResizeHandle side="left" onResize={resizeLeft} />}
      {!rightCollapsed && <ResizeHandle side="right" onResize={resizeRight} />}

      {selection && <div className="selection-popover" style={{ left: selection.x, top: selection.y }}>
        <div className="selection-source">{selection.role === 'user' ? '从我的背景材料提问' : '从 AI 回答提问'}</div>
        <div className="selection-preview">“{selection.text.slice(0, 80)}{selection.text.length > 80 ? '…' : ''}”</div>
        {busy && <div className="selection-status" role="status">请求已发送，正在等待模型创建分支…</div>}
        {!selection.custom ? <div className="selection-actions"><button disabled={busy} onClick={() => expand(null)}>直接解释</button><button disabled={busy} className="secondary" onClick={() => setSelection({ ...selection, custom: true })}>自定义问题</button><button disabled={busy} className="icon" onClick={() => setSelection(null)}>×</button></div> : <form onSubmit={(e) => { e.preventDefault(); if (customQuestion.trim()) expand(customQuestion) }}><input disabled={busy} autoFocus aria-label="自定义问题" value={customQuestion} onChange={(e) => setCustomQuestion(e.target.value)} placeholder="你想追问什么？" /><button disabled={busy || !customQuestion.trim()}>展开</button></form>}
      </div>}
      {settingsOpen && <ProviderSettings onClose={() => setSettingsOpen(false)} onChanged={refreshProviders} />}
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)
