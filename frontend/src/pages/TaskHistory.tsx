import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import type { TablePaginationConfig, TableColumnsType } from 'antd'
import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons'
import { PageHeader } from '@/components/PageHeader'
import { apiFetch } from '@/lib/utils'

const { Text } = Typography

interface TaskLogItem {
  id: number
  created_at: string
  platform: string
  email: string
  status: 'success' | 'failed' | 'skipped'
  error: string
}

interface TaskLogListResponse {
  total: number
  items: TaskLogItem[]
}

interface TaskLogBatchDeleteResponse {
  deleted: number
  not_found: number[]
  total_requested: number
}

const PLATFORM_OPTIONS = [
  { value: '', label: '全部平台' },
  { value: 'chatgpt', label: 'ChatGPT' },
  { value: 'grok', label: 'Grok' },
  { value: 'kiro', label: 'Kiro' },
  { value: 'openblocklabs', label: 'OpenBlockLabs' },
  { value: 'tavily', label: 'Tavily' },
  { value: 'trae', label: 'Trae' },
  { value: 'cursor', label: 'Cursor' },
]

function formatTime(value: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN')
}

export default function TaskHistory() {
  const [logs, setLogs] = useState<TaskLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [platform, setPlatform] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([])
  const [pagination, setPagination] = useState<TablePaginationConfig>({
    current: 1,
    pageSize: 20,
    showSizeChanger: true,
    pageSizeOptions: [20, 50, 100, 200],
  })

  const load = useCallback(async (next?: Partial<TablePaginationConfig>) => {
    const current = next?.current ?? pagination.current ?? 1
    const pageSize = next?.pageSize ?? pagination.pageSize ?? 20
    setLoading(true)
    try {
      const params = new URLSearchParams({
        page: String(current),
        page_size: String(pageSize),
      })
      if (platform) params.set('platform', platform)
      const data = await apiFetch(`/tasks/logs?${params.toString()}`) as TaskLogListResponse
      setLogs(data.items || [])
      setTotal(Number(data.total || 0))
      setPagination(prev => ({ ...prev, current, pageSize, total: Number(data.total || 0) }))
      setSelectedRowKeys(prev => prev.filter(key => (data.items || []).some(item => item.id === key)))
    } finally {
      setLoading(false)
    }
  }, [pagination.current, pagination.pageSize, platform])

  useEffect(() => {
    void load({ current: 1 })
  }, [load])

  const handleBatchDelete = async () => {
    if (!selectedRowKeys.length) return
    const result = await apiFetch('/tasks/logs/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids: selectedRowKeys }),
    }) as TaskLogBatchDeleteResponse
    message.success(`已删除 ${result.deleted} 条任务历史`)
    if (result.not_found.length > 0) {
      message.warning(`${result.not_found.length} 条记录不存在或已被删除`)
    }
    setSelectedRowKeys([])
    await load()
  }

  const columns: TableColumnsType<TaskLogItem> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (text: string) => <Text type="secondary">{formatTime(text)}</Text>,
    },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 110,
      render: (text: string) => <Tag>{text || '-'}</Tag>,
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      render: (text: string) => (
        <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{text || '-'}</span>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: string) => (
        <Tag color={status === 'success' ? 'success' : status === 'failed' ? 'error' : 'gold'}>
          {status === 'success' ? '成功' : status === 'failed' ? '失败' : '跳过'}
        </Tag>
      ),
    },
    {
      title: '错误信息',
      dataIndex: 'error',
      key: 'error',
      ellipsis: true,
      render: (text: string) => text || '-',
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <PageHeader
        eyebrow="任务历史"
        title="统一查看任务日志，并按页治理历史数据"
        description="服务端分页加载，避免一次性拉全量日志；适合清理和追踪执行结果。"
        extra={
          <Space wrap>
            <Text type="secondary">{total} 条记录</Text>
            {selectedRowKeys.length > 0 ? <Text type="success">已选 {selectedRowKeys.length} 条</Text> : null}
            <Select
              value={platform}
              onChange={(value) => {
                setPlatform(value)
                setSelectedRowKeys([])
              }}
              style={{ width: 170 }}
              options={PLATFORM_OPTIONS}
            />
            {selectedRowKeys.length > 0 ? (
              <Popconfirm
                title={`确认删除选中的 ${selectedRowKeys.length} 条任务历史？`}
                onConfirm={() => void handleBatchDelete()}
              >
                <Button danger icon={<DeleteOutlined />}>
                  删除 {selectedRowKeys.length} 条
                </Button>
              </Popconfirm>
            ) : null}
            <Button icon={<ReloadOutlined spin={loading} />} onClick={() => void load()} loading={loading}>
              刷新
            </Button>
          </Space>
        }
      />

      <Card bordered={false}>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={logs}
          loading={loading}
          rowSelection={{
            selectedRowKeys,
            onChange: keys => setSelectedRowKeys(keys as number[]),
          }}
          pagination={pagination}
          onChange={(pager) => {
            void load({ current: pager.current, pageSize: pager.pageSize })
          }}
        />
      </Card>
    </div>
  )
}
