import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Input, Space, Table, Tag, Typography, message } from 'antd'
import { InboxOutlined, UploadOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import { apiFetch } from '@/lib/utils'

type PoolItem = {
  index: number
  email: string
  mailbox?: string
}

type PoolSnapshot = {
  pool_dir: string
  filename: string
  path: string
  count: number
  items: PoolItem[]
  truncated: boolean
}

function useIsImportRoute() {
  const location = useLocation()
  return location.pathname.endsWith('/import')
}

function useIsOutlookRoute() {
  const location = useLocation()
  return location.pathname.startsWith('/mailpool/outlook')
}

function EmailPoolList() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [snapshot, setSnapshot] = useState<PoolSnapshot | null>(null)
  const [poolDir, setPoolDir] = useState('')
  const [poolFile, setPoolFile] = useState('')
  const [search, setSearch] = useState('')
  const [limit, setLimit] = useState(500)

  const loadSnapshot = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (String(poolDir || '').trim()) params.set('pool_dir', String(poolDir || '').trim())
      if (String(poolFile || '').trim()) params.set('pool_file', String(poolFile || '').trim())
      params.set('preview_limit', String(limit))
      const result = await apiFetch(`/config/applemail/pool?${params.toString()}`)
      setSnapshot(result)
      if (!poolDir) setPoolDir(result.pool_dir || '')
      if (!poolFile) setPoolFile(result.filename || '')
    } catch (error: unknown) {
      setSnapshot(null)
      const msg = error instanceof Error ? error.message : '加载邮箱池失败'
      message.error(msg || '加载邮箱池失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadSnapshot()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const filteredItems = useMemo(() => {
    const items = snapshot?.items || []
    const q = String(search || '').trim().toLowerCase()
    if (!q) return items
    return items.filter(item => {
      const email = String(item.email || '').toLowerCase()
      const mailbox = String(item.mailbox || '').toLowerCase()
      return email.includes(q) || mailbox.includes(q)
    })
  }, [snapshot, search])

  const columns: ColumnsType<PoolItem> = [
    { title: '#', dataIndex: 'index', width: 70 },
    { title: '邮箱', dataIndex: 'email' },
    { title: '邮箱夹', dataIndex: 'mailbox', width: 140, render: v => <Tag>{String(v || 'INBOX')}</Tag> },
  ]

  return (
    <Card
      title="已导入邮箱列表（AppleMail）"
      extra={
        <Space size={8}>
          <Button icon={<ReloadOutlined />} onClick={() => void loadSnapshot()} loading={loading}>
            刷新
          </Button>
          <Button type="primary" icon={<UploadOutlined />} onClick={() => navigate('/mailpool/import')}>
            导入邮箱
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Tag color="blue">已导入: {snapshot?.count || 0} 个</Tag>
            {snapshot?.filename ? <Typography.Text type="secondary">当前文件: {snapshot.filename}</Typography.Text> : null}
            {snapshot?.pool_dir ? <Typography.Text type="secondary">目录: {snapshot.pool_dir}</Typography.Text> : null}
          </Space>
          {snapshot?.truncated ? (
            <Typography.Text type="secondary">仅预览前 {limit} 条（可调整预览数量）</Typography.Text>
          ) : null}
        </Space>

        <Space wrap style={{ width: '100%' }}>
          <Input
            value={poolDir}
            onChange={e => setPoolDir(e.target.value)}
            placeholder="pool_dir（可选，默认 mail）"
            style={{ width: 260 }}
          />
          <Input
            value={poolFile}
            onChange={e => setPoolFile(e.target.value)}
            placeholder="pool_file（可选，默认读取最新文件）"
            style={{ width: 320 }}
          />
          <Input
            value={String(limit)}
            onChange={e => {
              const raw = Number(String(e.target.value || '').trim())
              if (!Number.isFinite(raw)) return
              setLimit(Math.min(Math.max(Math.floor(raw), 10), 5000))
            }}
            placeholder="预览数量（10~5000）"
            style={{ width: 180 }}
          />
          <Button onClick={() => void loadSnapshot()} loading={loading}>
            应用
          </Button>
          <Input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="搜索邮箱 / 邮箱夹"
            style={{ width: 240, marginLeft: 'auto' }}
          />
        </Space>

        <Table
          rowKey={row => `${row.index}-${row.email}`}
          columns={columns}
          dataSource={filteredItems}
          loading={loading}
          size="middle"
          pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        />
      </Space>
    </Card>
  )
}

function EmailPoolImport() {
  const navigate = useNavigate()
  const [importing, setImporting] = useState(false)
  const [content, setContent] = useState('')
  const [filename, setFilename] = useState('')
  const [poolDir, setPoolDir] = useState('mail')

  const handlePickFile = async () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.json,.txt,text/plain,application/json'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        const text = await file.text()
        setContent(text)
        setFilename(file.name)
      } catch (error: unknown) {
        const msg = error instanceof Error ? error.message : '读取文件失败'
        message.error(msg || '读取文件失败')
      }
    }
    input.click()
  }

  const handleImport = async () => {
    if (!content.trim()) {
      message.error('请选择文件或粘贴内容')
      return
    }
    setImporting(true)
    try {
      const result = await apiFetch('/config/applemail/import', {
        method: 'POST',
        body: JSON.stringify({
          content,
          filename,
          pool_dir: String(poolDir || 'mail').trim() || 'mail',
          bind_to_config: true,
        }),
      })
      message.success(`导入成功，共 ${result.count} 个邮箱，已绑定 ${result.filename}`)
      navigate('/mailpool')
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '导入失败'
      message.error(msg || '导入失败')
    } finally {
      setImporting(false)
    }
  }

  return (
    <Card
      title="导入邮箱（AppleMail）"
      extra={
        <Space size={8}>
          <Button onClick={() => navigate('/mailpool')}>返回列表</Button>
          <Button type="primary" onClick={handleImport} loading={importing}>
            确认导入
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Typography.Text type="secondary">
          支持 JSON 或 TXT。TXT 每行一条，字段用 `----` / TAB / 空格分隔；必须包含 `email + client_id + refresh_token`（可选
          password、mailbox）。示例：`demo@example.com----password----client_id----refresh_token----INBOX`
        </Typography.Text>

        <Space wrap style={{ width: '100%' }}>
          <Input
            value={poolDir}
            onChange={e => setPoolDir(e.target.value)}
            placeholder="pool_dir（默认 mail）"
            style={{ width: 260 }}
          />
          <Input
            value={filename}
            onChange={e => setFilename(e.target.value)}
            placeholder="可选文件名（留空自动生成 applemail_时间.json）"
            style={{ width: 420 }}
          />
          <Button icon={<InboxOutlined />} onClick={() => void handlePickFile()}>
            选择文件
          </Button>
          <Button
            danger
            onClick={() => {
              setContent('')
              setFilename('')
            }}
          >
            清空
          </Button>
        </Space>

        <Input.TextArea
          value={content}
          onChange={e => setContent(e.target.value)}
          rows={14}
          placeholder={'[\n  {\n    "email": "demo@example.com",\n    "clientId": "xxxx",\n    "refreshToken": "xxxx",\n    "password": "可选",\n    "folder": "INBOX"\n  }\n]\n\n或 TXT:\ndemo@example.com----password----client_id----refresh_token----INBOX'}
          style={{ fontFamily: 'monospace' }}
        />
      </Space>
    </Card>
  )
}

type OutlookAccount = {
  id: number
  email: string
  enabled: boolean
  has_oauth: boolean
  created_at: string
  updated_at: string
  last_used?: string | null
}

type OutlookListResponse = {
  total: number
  items: OutlookAccount[]
}

function OutlookList() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<OutlookListResponse | null>(null)
  const [q, setQ] = useState('')
  const [enabled, setEnabled] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (String(q || '').trim()) params.set('q', String(q || '').trim())
      if (String(enabled || '').trim()) params.set('enabled', String(enabled || '').trim())
      params.set('page', '1')
      params.set('page_size', '200')
      const res = await apiFetch(`/outlook?${params.toString()}`)
      setData(res)
    } catch (error: unknown) {
      setData(null)
      const msg = error instanceof Error ? error.message : '加载 Outlook 账号失败'
      message.error(msg || '加载 Outlook 账号失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleToggle = async (row: OutlookAccount) => {
    try {
      await apiFetch(`/outlook/${row.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled: !row.enabled }),
      })
      void load()
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '更新失败'
      message.error(msg || '更新失败')
    }
  }

  const handleDelete = async (row: OutlookAccount) => {
    try {
      await apiFetch(`/outlook/${row.id}`, { method: 'DELETE' })
      void load()
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '删除失败'
      message.error(msg || '删除失败')
    }
  }

  const handleExport = async () => {
    try {
      const params = new URLSearchParams()
      if (String(enabled || '').trim()) params.set('enabled', String(enabled || '').trim())
      const res = await fetch(`/api/outlook/export?${params.toString()}`)
      if (!res.ok) throw new Error(await res.text())
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'outlook_accounts.txt'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '导出失败'
      message.error(msg || '导出失败')
    }
  }

  const columns: ColumnsType<OutlookAccount> = [
    { title: '邮箱', dataIndex: 'email' },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 90,
      render: v => (v ? <Tag color="green">启用</Tag> : <Tag>禁用</Tag>),
    },
    {
      title: 'OAuth',
      dataIndex: 'has_oauth',
      width: 90,
      render: v => (v ? <Tag color="blue">有</Tag> : <Tag>无</Tag>),
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_, row) => (
        <Space size={8}>
          <Button size="small" onClick={() => void handleToggle(row)}>
            {row.enabled ? '禁用' : '启用'}
          </Button>
          <Button size="small" danger onClick={() => void handleDelete(row)}>
            删除
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="Outlook（本地导入）账号列表"
      extra={
        <Space size={8}>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
          <Button onClick={() => void handleExport()}>导出</Button>
          <Button type="primary" icon={<UploadOutlined />} onClick={() => navigate('/mailpool/outlook/import')}>
            导入邮箱
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Tag color="blue">已导入: {data?.total || 0} 个</Tag>
          </Space>
          <Space wrap>
            <Input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="搜索邮箱"
              style={{ width: 240 }}
            />
            <Input
              value={enabled}
              onChange={e => setEnabled(e.target.value)}
              placeholder="enabled: true/false（可选）"
              style={{ width: 220 }}
            />
            <Button onClick={() => void load()} loading={loading}>
              应用
            </Button>
          </Space>
        </Space>

        <Table
          rowKey={row => String(row.id)}
          columns={columns}
          dataSource={data?.items || []}
          loading={loading}
          pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        />
      </Space>
    </Card>
  )
}

function OutlookImport() {
  const navigate = useNavigate()
  const [importing, setImporting] = useState(false)
  const [value, setValue] = useState('')

  const handlePickFile = async () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.txt,text/plain'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        setValue(await file.text())
      } catch (error: unknown) {
        const msg = error instanceof Error ? error.message : '读取文件失败'
        message.error(msg || '读取文件失败')
      }
    }
    input.click()
  }

  const handleImport = async () => {
    const payload = String(value || '').trim()
    if (!payload) {
      message.error('请选择文件或粘贴内容')
      return
    }
    setImporting(true)
    try {
      const res = await apiFetch('/outlook/batch-import', {
        method: 'POST',
        body: JSON.stringify({ data: payload, enabled: true }),
      })
      message.success(`导入完成：成功 ${res.success} / 失败 ${res.failed}`)
      navigate('/mailpool/outlook')
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '导入失败'
      message.error(msg || '导入失败')
    } finally {
      setImporting(false)
    }
  }

  return (
    <Card
      title="导入邮箱（Outlook 本地导入）"
      extra={
        <Space size={8}>
          <Button onClick={() => navigate('/mailpool/outlook')}>返回列表</Button>
          <Button type="primary" onClick={handleImport} loading={importing}>
            确认导入
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Typography.Text type="secondary">
          每行格式：`邮箱----密码` 或 `邮箱----密码----client_id----refresh_token`（与设置页的 Outlook 批量导入一致）。
        </Typography.Text>
        <Space wrap>
          <Button icon={<InboxOutlined />} onClick={() => void handlePickFile()}>
            选择 TXT 文件
          </Button>
          <Button
            danger
            onClick={() => {
              setValue('')
            }}
          >
            清空
          </Button>
        </Space>
        <Input.TextArea
          value={value}
          onChange={e => setValue(e.target.value)}
          rows={14}
          placeholder={'example@outlook.com----password\nexample@outlook.com----password----client_id----refresh_token'}
          style={{ fontFamily: 'monospace' }}
        />
      </Space>
    </Card>
  )
}

export default function EmailPool() {
  const isImportRoute = useIsImportRoute()
  const isOutlookRoute = useIsOutlookRoute()
  if (isOutlookRoute) {
    return isImportRoute ? <OutlookImport /> : <OutlookList />
  }
  return isImportRoute ? <EmailPoolImport /> : <EmailPoolList />
}
