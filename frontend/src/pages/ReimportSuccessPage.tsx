import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Input, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '@/lib/utils'

type ReimportSuccessRow = {
  email: string
  fail_count: number
  platforms: string[]
  latest_error: string
  latest_reason: string
  latest_created_at?: string | null
  reimport_attempts: number
  reimport_imported_count: number
  reimport_duplicate_count: number
  success_after_reimport: boolean
  reimports_before_success: number
  first_success_at?: string | null
}

type ReimportSuccessResponse = {
  total: number
  items: ReimportSuccessRow[]
}

export default function ReimportSuccessPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [rows, setRows] = useState<ReimportSuccessRow[]>([])

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (String(search || '').trim()) params.set('q', String(search || '').trim())
      const res = await apiFetch(`/tasks/failed-emails/reimport-success?${params.toString()}`)
      const data = (res || {}) as ReimportSuccessResponse
      setRows(Array.isArray(data.items) ? data.items : [])
    } catch (error: unknown) {
      setRows([])
      const msg = error instanceof Error ? error.message : '加载回流成功列表失败'
      message.error(msg || '加载回流成功列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const totalReimports = useMemo(() => rows.reduce((sum, row) => sum + Number(row.reimport_attempts || 0), 0), [rows])

  const columns: ColumnsType<ReimportSuccessRow> = [
    {
      title: '邮箱',
      dataIndex: 'email',
      render: (value: string, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text strong>{value}</Typography.Text>
          <Space size={[4, 4]} wrap>
            {(row.platforms || []).map(item => (
              <Tag key={item}>{item}</Tag>
            ))}
          </Space>
        </Space>
      ),
    },
    {
      title: '历史失败次数',
      dataIndex: 'fail_count',
      width: 120,
      render: (value: number) => <Tag color="red">{value}</Tag>,
    },
    {
      title: '回流情况',
      key: 'reimport',
      width: 200,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text>回流 {row.reimport_attempts || 0} 次</Typography.Text>
          <Typography.Text type="secondary">
            导入成功 {row.reimport_imported_count || 0} / 重复 {row.reimport_duplicate_count || 0}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '回流后成功',
      key: 'success',
      width: 220,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Tag color="green">已成功</Tag>
          <Typography.Text>成功前回流 {row.reimports_before_success || 0} 次</Typography.Text>
          <Typography.Text type="secondary">{row.first_success_at || '-'}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '最近失败原因',
      dataIndex: 'latest_reason',
      width: 220,
      render: (value: string, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text>{value || '其他失败'}</Typography.Text>
          {row.latest_error ? <Typography.Text type="secondary">{row.latest_error}</Typography.Text> : null}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_, row) => (
        <Button size="small" onClick={() => navigate(`/mailpool/failed?q=${encodeURIComponent(row.email)}`)}>
          查看失败记录
        </Button>
      ),
    },
  ]

  return (
    <Card
      title="回流后成功邮箱"
      extra={
        <Space size={8}>
          <Button onClick={() => navigate('/mailpool/failed/retry-over-2')}>返回失败重试页</Button>
          <Button onClick={() => navigate('/register?use_outlook_pool=1&outlook_failed_reimport_only=1')}>
            再次执行失败回流注册
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Tag color="green">回流后成功：{rows.length} 个</Tag>
            <Tag color="blue">累计回流 {totalReimports} 次</Tag>
            <Typography.Text type="secondary">聚焦已经通过回流重新激活成功的邮箱。</Typography.Text>
          </Space>
          <Space wrap>
            <Input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="搜索邮箱 / 平台 / 错误"
              style={{ width: 280 }}
              onPressEnter={() => void load()}
            />
            <Button onClick={() => void load()} loading={loading}>查询</Button>
          </Space>
        </Space>

        <Table
          rowKey={row => row.email}
          columns={columns}
          dataSource={rows}
          loading={loading}
          pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        />
      </Space>
    </Card>
  )
}
