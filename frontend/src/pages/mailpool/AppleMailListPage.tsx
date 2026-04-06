import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Input, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
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

export default function AppleMailListPage() {
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
          <Input value={poolDir} onChange={e => setPoolDir(e.target.value)} placeholder="pool_dir（可选，默认 mail）" style={{ width: 260 }} />
          <Input value={poolFile} onChange={e => setPoolFile(e.target.value)} placeholder="pool_file（可选，默认读取最新文件）" style={{ width: 320 }} />
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
          <Button onClick={() => void loadSnapshot()} loading={loading}>应用</Button>
          <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索邮箱 / 邮箱夹" style={{ width: 240, marginLeft: 'auto' }} />
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
