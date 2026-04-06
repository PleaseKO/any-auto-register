export type OutlookImportParsedRow = {
  index: number
  raw: string
  email: string
  password: string
  client_id: string
  refresh_token: string
  valid: boolean
  reason: string
  duplicate: boolean
}

export function parseOutlookImportText(text: string): OutlookImportParsedRow[] {
  const rows: OutlookImportParsedRow[] = []
  const emailCounter = new Map<string, number>()
  const lines = String(text || '').split(/\r?\n/)

  lines.forEach((raw, lineIndex) => {
    const line = String(raw || '').trim()
    if (!line) return
    const parts = line.split('----').map(part => part.trim())
    const email = String(parts[0] || '').trim()
    const password = String(parts[1] || '').trim()
    const client_id = String(parts[2] || '').trim()
    const refresh_token = String(parts[3] || '').trim()

    let valid = true
    let reason = ''
    if (!email || !password) {
      valid = false
      reason = '缺少邮箱或密码'
    } else if (!email.includes('@')) {
      valid = false
      reason = '邮箱格式无效'
    }

    const key = email.toLowerCase()
    const seen = key ? (emailCounter.get(key) || 0) : 0
    emailCounter.set(key, seen + 1)

    rows.push({
      index: lineIndex + 1,
      raw: line,
      email,
      password,
      client_id,
      refresh_token,
      valid,
      reason,
      duplicate: false,
    })
  })

  return rows.map(row => ({
    ...row,
    duplicate: Boolean(row.email && (emailCounter.get(row.email.toLowerCase()) || 0) > 1),
  }))
}

export function buildCleanOutlookImportPayload(rows: OutlookImportParsedRow[]) {
  const unique = new Map<string, OutlookImportParsedRow>()
  for (const row of rows) {
    if (!row.valid) continue
    const key = row.email.toLowerCase()
    unique.set(key, row)
  }
  const cleanedRows = Array.from(unique.values())
  return cleanedRows
    .map(row => {
      if (row.client_id && row.refresh_token) {
        return `${row.email}----${row.password}----${row.client_id}----${row.refresh_token}`
      }
      return `${row.email}----${row.password}`
    })
    .join('\n')
}
