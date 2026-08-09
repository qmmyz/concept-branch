const items = document.querySelector('#items')

function textElement(tag, text) {
  const element = document.createElement(tag)
  element.textContent = text
  return element
}

fetch('/api/frontends')
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return response.json()
  })
  .then(({ frontends }) => {
    items.replaceChildren()
    for (const frontend of frontends) {
      const card = document.createElement('div')
      card.className = `card${frontend.available ? '' : ' missing'}`
      const summary = document.createElement('span')
      summary.append(textElement('strong', frontend.label), document.createElement('br'), textElement('small', frontend.available ? '可用' : '尚未生成'))
      card.append(summary)
      if (frontend.available && typeof frontend.url === 'string' && frontend.url.startsWith('/')) {
        const link = textElement('a', '打开')
        link.href = frontend.url
        card.append(link)
      } else {
        card.append(textElement('span', '—'))
      }
      items.append(card)
    }
  })
  .catch(() => {
    items.textContent = '加载失败，请先登录后重试。'
  })
