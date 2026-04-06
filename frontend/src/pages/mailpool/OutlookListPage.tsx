import { useEffect, useState } from 'react'
import { Button, Card, Input, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '@/lib/utils'

type OutlookAccount = {
  id: number
  email: string
  enabled: boolean
  has_oauth: boolean
  source_tag: string
  created_at: string
  updated_at: string
  last_used?: string | null
}

type OutlookListResponse = {
  total: number
  items: OutlookAccount[]
}

function renderSourceTag(sourceTag: string) {
  const normalized = String(sourceTag || '').trim().toLowerCase()
  if (normalized === 'failed_reimport') {
    return <Tag color="volcano">失败回流</Tag>
  }
  if (normalized === 'register_machine') {
    return <Tag color="geekblue">注册机上传</Tag>
  }
  return <Tag>普通导入</Tag>
}

export default function OutlookListPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<OutlookListResponse | null>(null)
  const [q, setQ] = useState('')
  const [enabled, setEnabled] = useState('')
  const [sourceTag, setSourceTag] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (String(q || '').trim()) params.set('q', String(q || '').trim())
      if (String(enabled || '').trim()) params.set('enabled', String(enabled || '').trim())
      if (String(sourceTag || '').trim()) params.set('source_tag', String(sourceTag || '').trim())
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
      if (String(sourceTag || '').trim()) params.set('source_tag', String(sourceTag || '').trim())
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
      title: '标签',
      dataIndex: 'source_tag',
      width: 100,
      render: (v: string) => renderSourceTag(v),
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_, row) => (
        <Space size={8}>
          <Button size="small" onClick={() => void handleToggle(row)}>{row.enabled ? '禁用' : '启用'}</Button>
          <Button size="small" danger onClick={() => void handleDelete(row)}>删除</Button>
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="Outlook（本地导入）账号列表"
      extra={
        <Space size={8}>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>刷新</Button>
          <Button onClick={() => void handleExport()}>导出</Button>
          <Button type="primary" icon={<UploadOutlined />} onClick={() => navigate('/mailpool/outlook/import')}>导入邮箱</Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Tag color="blue">已导入: {data?.total || 0} 个</Tag>
            <Tag color="volcano">失败回流: {(data?.items || []).filter(item => item.source_tag === 'failed_reimport').length} 个</Tag>
            <Tag color="geekblue">注册机上传: {(data?.items || []).filter(item => item.source_tag === 'register_machine').length} 个</Tag>
            <Typography.Text type="secondary">支持本地 TXT 批量导入、失败邮箱回流与注册机上传</Typography.Text>
          </Space>
          <Space wrap>
            <Input value={q} onChange={e => setQ(e.target.value)} placeholder="搜索邮箱" style={{ width: 240 }} />
            <Input value={enabled} onChange={e => setEnabled(e.target.value)} placeholder="enabled: true/false（可选）" style={{ width: 220 }} />
            <Input value={sourceTag} onChange={e => setSourceTag(e.target.value)} placeholder="source_tag: failed_reimport / register_machine（可选）" style={{ width: 320 }} />
            <Button onClick={() => void load()} loading={loading}>应用</Button>
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
