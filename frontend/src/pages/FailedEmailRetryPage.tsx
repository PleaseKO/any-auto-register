import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Input, Segmented, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '@/lib/utils'
import { BatchResultDialog, type BatchResult } from '@/components/BatchResultDialog'

type FailedEmailRetryRow = {
  email: string
  password: string
  client_id: string
  refresh_token: string
  fail_count: number
  platform_count: number
  platforms: string[]
  latest_log_id: number
  latest_error: string
  latest_reason: string
  latest_created_at?: string | null
  importable: boolean
  has_oauth: boolean
  source_mode: string
  source_label: string
  reimport_attempts: number
  reimport_imported_count: number
  reimport_duplicate_count: number
  success_after_reimport: boolean
  reimports_before_success: number
  first_success_at?: string | null
}

type FailedEmailRetryResponse = {
  total: number
  min_retry_count: number
  items: FailedEmailRetryRow[]
}

export default function FailedEmailRetryPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [rows, setRows] = useState<FailedEmailRetryRow[]>([])
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [dialogOpen, setDialogOpen] = useState(false)
  const [result, setResult] = useState<BatchResult | null>(null)
  const [mode, setMode] = useState<'1' | '2' | '3+'>('3+')

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (mode === '1') {
        params.set('exact_retry_count', '1')
      } else if (mode === '2') {
        params.set('exact_retry_count', '2')
      } else {
        params.set('min_retry_count', '3')
      }
      if (String(search || '').trim()) params.set('q', String(search || '').trim())
      const res = await apiFetch(`/tasks/failed-emails/retry-summary?${params.toString()}`)
      const data = (res || {}) as FailedEmailRetryResponse
      setRows(Array.isArray(data.items) ? data.items : [])
    } catch (error: unknown) {
      setRows([])
      const msg = error instanceof Error ? error.message : '加载失败重试列表失败'
      message.error(msg || '加载失败重试列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  const totalFails = useMemo(() => rows.reduce((sum, row) => sum + Number(row.fail_count || 0), 0), [rows])
  const selectedRows = useMemo(() => {
    const keys = new Set(selectedRowKeys.map(String))
    return rows.filter(row => keys.has(String(row.email)))
  }, [rows, selectedRowKeys])
  const title = mode === '1' ? '失败 1 次的邮箱' : mode === '2' ? '失败 2 次的邮箱' : '失败重试超过 2 次的邮箱'
  const countLabel = mode === '1' ? '失败1次' : mode === '2' ? '失败2次' : '失败次数 > 2'

  const buildImportLine = (row: FailedEmailRetryRow): string | null => {
    const email = String(row.email || '').trim()
    const password = String(row.password || '').trim()
    if (!email || !password) return null
    const clientId = String(row.client_id || '').trim()
    const refreshToken = String(row.refresh_token || '').trim()
    if (clientId && refreshToken) return `${email}----${password}----${clientId}----${refreshToken}`
    return `${email}----${password}`
  }

  const handleImportToOutlook = async () => {
    if (!selectedRows.length) {
      message.warning('请先勾选要回流的邮箱')
      return
    }

    const lines = selectedRows.map(buildImportLine).filter((line): line is string => Boolean(line))
    const skipped = selectedRows.length - lines.length
    if (!lines.length) {
      message.warning('所选记录缺少邮箱或密码，无法导入 Outlook 列表')
      return
    }

    try {
      const res = await apiFetch('/outlook/batch-import', {
        method: 'POST',
        body: JSON.stringify({ data: lines.join('\n'), enabled: true, source: 'failed_email_retry_page', source_tag: 'failed_reimport' }),
      })
      const nextResult: BatchResult = {
        title: '失败邮箱回流结果',
        total: selectedRows.length,
        success: Number(res?.success || 0),
        failed: Number(res?.failed || 0),
        skipped,
        errors: Array.isArray(res?.errors) ? res.errors : [],
        preview: lines.join('\n'),
      }
      setResult(nextResult)
      setDialogOpen(true)
      if (Number(res?.success || 0) <= 0) {
        const firstError = Array.isArray(res?.errors) && res.errors.length ? `：${res.errors[0]}` : ''
        message.error(`导入失败：成功 0 / 失败 ${res?.failed || 0}（跳过 ${skipped}）${firstError}`)
        return
      }
      if (Number(res?.failed || 0) > 0) {
        const firstError = Array.isArray(res?.errors) && res.errors.length ? `；首条错误：${res.errors[0]}` : ''
        message.warning(`部分导入成功：成功 ${res.success} / 失败 ${res.failed}（跳过 ${skipped}）${firstError}`)
      } else {
        message.success(`导入完成：成功 ${res.success} / 失败 ${res.failed}（跳过 ${skipped}）`)
      }
      navigate('/mailpool/outlook')
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '导入失败'
      message.error(msg || '导入失败')
    }
  }

  const columns: ColumnsType<FailedEmailRetryRow> = [
    {
      title: '邮箱',
      dataIndex: 'email',
      render: (value: string, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text strong>{value}</Typography.Text>
          <Space size={4} wrap>
            {row.importable ? <Tag color="green">可回流</Tag> : <Tag>缺少密码</Tag>}
            {row.has_oauth ? <Tag color="blue">有 OAuth</Tag> : null}
            {row.source_mode ? <Tag color="gold">{row.source_label || '推断补录'}</Tag> : null}
          </Space>
        </Space>
      ),
    },
    {
      title: '失败次数',
      dataIndex: 'fail_count',
      width: 110,
      sorter: (a, b) => a.fail_count - b.fail_count,
      defaultSortOrder: 'descend',
      render: (value: number) => <Tag color="red">{value}</Tag>,
    },
    {
      title: '平台',
      dataIndex: 'platforms',
      width: 220,
      render: (value: string[]) => (
        <Space size={[4, 4]} wrap>
          {(value || []).map(item => (
            <Tag key={item}>{item}</Tag>
          ))}
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
      title: '最近失败时间',
      dataIndex: 'latest_created_at',
      width: 190,
      render: (value?: string | null) => value || '-',
    },
    {
      title: '回流记录',
      key: 'reimport',
      width: 180,
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
      key: 'success_after_reimport',
      width: 180,
      render: (_, row) =>
        row.success_after_reimport ? (
          <Space direction="vertical" size={2}>
            <Tag color="green">已成功</Tag>
            <Typography.Text type="secondary">成功前回流 {row.reimports_before_success || 0} 次</Typography.Text>
            <Typography.Text type="secondary">{row.first_success_at || '-'}</Typography.Text>
          </Space>
        ) : (
          <Tag>未记录成功</Tag>
        ),
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      render: (_, row) => (
        <Button size="small" onClick={() => navigate(`/mailpool/failed?q=${encodeURIComponent(row.email)}`)}>
          查看失败记录
        </Button>
      ),
    },
  ]

  return (
    <Card
      title={title}
      extra={
        <Space size={8}>
          <Button onClick={() => navigate('/mailpool/failed')}>返回失败邮箱</Button>
          <Button onClick={() => navigate('/mailpool/reimport-success')}>查看回流后成功</Button>
          <Button onClick={() => navigate('/register?use_outlook_pool=1&outlook_failed_reimport_only=1')}>
            执行失败回流注册
          </Button>
          <Button type="primary" onClick={() => void handleImportToOutlook()}>
            回流导入 Outlook
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
            <Segmented
              value={mode}
              onChange={value => setMode(String(value) as '1' | '2' | '3+')}
              options={[
                { label: '1次', value: '1' },
                { label: '2次', value: '2' },
                { label: '3次及以上', value: '3+' },
              ]}
            />
            <Tag color="red">{countLabel}：{rows.length} 个</Tag>
            <Tag color="orange">累计失败 {totalFails} 次</Tag>
            <Typography.Text type="secondary">按邮箱聚合统计，方便筛出首次失败和反复失败的邮箱。</Typography.Text>
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
          loading={loading}
          columns={columns}
          dataSource={rows}
          rowSelection={{
            selectedRowKeys,
            onChange: keys => setSelectedRowKeys(keys),
          }}
          pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        />
      </Space>
      <BatchResultDialog
        open={dialogOpen}
        result={result}
        onClose={() => setDialogOpen(false)}
      />
    </Card>
  )
}
