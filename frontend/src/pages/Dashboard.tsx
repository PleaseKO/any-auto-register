import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Col, Progress, Row, Space, Spin, Statistic, Tag } from 'antd'
import {
  AlertOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  MailOutlined,
  ReloadOutlined,
  UserOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'

const PLATFORM_COLORS: Record<string, string> = {
  trae: '#3b82f6',
  cursor: '#10b981',
  chatgpt: '#6366f1',
  grok: '#8b5cf6',
  kiro: '#f59e0b',
  openblocklabs: '#06b6d4',
}

const STATUS_COLORS: Record<string, string> = {
  registered: 'default',
  trial: 'success',
  subscribed: 'success',
  expired: 'warning',
  invalid: 'error',
}

type DashboardStats = {
  total: number
  by_platform: Record<string, number>
  by_status: Record<string, number>
  task_logs?: {
    total: number
    by_status: Record<string, number>
    failed_email_pool?: {
      total: number
      importable: number
      inferred: number
    }
  }
  outlook_pool?: {
    total: number
    enabled: number
    oauth: number
  }
}

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/accounts/stats')
      setStats(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const platformRows = useMemo(
    () => Object.entries(stats?.by_platform || {}).sort((a, b) => b[1] - a[1]),
    [stats],
  )
  const statusRows = useMemo(
    () => Object.entries(stats?.by_status || {}).sort((a, b) => b[1] - a[1]),
    [stats],
  )

  const statCards = [
    {
      title: '总账号数',
      value: stats?.total ?? 0,
      icon: <UserOutlined style={{ fontSize: 28 }} />,
      color: '#6366f1',
    },
    {
      title: '试用中',
      value: stats?.by_status?.trial ?? 0,
      icon: <ClockCircleOutlined style={{ fontSize: 28 }} />,
      color: '#f59e0b',
    },
    {
      title: '已订阅',
      value: stats?.by_status?.subscribed ?? 0,
      icon: <CheckCircleOutlined style={{ fontSize: 28 }} />,
      color: '#10b981',
    },
    {
      title: '失败邮箱可回流',
      value: stats?.task_logs?.failed_email_pool?.importable ?? 0,
      icon: <WarningOutlined style={{ fontSize: 28 }} />,
      color: '#ef4444',
    },
    {
      title: 'Outlook 池',
      value: stats?.outlook_pool?.total ?? 0,
      icon: <MailOutlined style={{ fontSize: 28 }} />,
      color: '#06b6d4',
    },
    {
      title: '推断补录失败',
      value: stats?.task_logs?.failed_email_pool?.inferred ?? 0,
      icon: <AlertOutlined style={{ fontSize: 28 }} />,
      color: '#8b5cf6',
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <PageHeader
        eyebrow="系统概览"
        title="用运营视角看账号池、失败池与 Outlook 资源"
        description="统一查看账号资产、失败回流能力和 Outlook 池健康度，减少页面之间来回切换。"
        extra={
          <Button icon={<ReloadOutlined spin={loading} />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        }
      />

      <Row gutter={[16, 16]}>
        {statCards.map(({ title, value, icon, color }) => (
          <Col xs={24} sm={12} xl={8} key={title}>
            <Card bordered={false}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Statistic title={title} value={value} />
                <div style={{ color, opacity: 0.88 }}>{icon}</div>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card
            bordered={false}
            title="平台分布"
            extra={<Tag color="blue">{platformRows.length} 个平台</Tag>}
          >
            {loading ? (
              <div style={{ textAlign: 'center', padding: 40 }}>
                <Spin />
              </div>
            ) : (
              platformRows.map(([platform, count]) => (
                <div key={platform} style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <Tag color={PLATFORM_COLORS[platform] || 'default'}>{platform}</Tag>
                    <span>{count}</span>
                  </div>
                  <Progress
                    percent={stats?.total ? Math.round((count / stats.total) * 100) : 0}
                    strokeColor={PLATFORM_COLORS[platform] || '#6366f1'}
                    showInfo={false}
                  />
                </div>
              ))
            )}
          </Card>
        </Col>

        <Col xs={24} xl={12}>
          <Card
            bordered={false}
            title="状态分布"
            extra={<Tag color="purple">{stats?.total ?? 0} 个账号</Tag>}
          >
            {loading ? (
              <div style={{ textAlign: 'center', padding: 40 }}>
                <Spin />
              </div>
            ) : (
              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                {statusRows.map(([status, count]) => (
                  <div
                    key={status}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '10px 0',
                      borderBottom: '1px solid rgba(148,163,184,0.16)',
                    }}
                  >
                    <Tag color={STATUS_COLORS[status] || 'default'}>{status}</Tag>
                    <strong>{count}</strong>
                  </div>
                ))}
              </Space>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card bordered={false} title="失败日志健康度">
            <Space direction="vertical" size={14} style={{ width: '100%' }}>
              <Statistic title="任务日志总数" value={stats?.task_logs?.total ?? 0} />
              <Statistic title="失败日志" value={stats?.task_logs?.by_status?.failed ?? 0} />
              <Statistic title="可回流失败邮箱" value={stats?.task_logs?.failed_email_pool?.importable ?? 0} />
            </Space>
          </Card>
        </Col>

        <Col xs={24} xl={12}>
          <Card bordered={false} title="Outlook 池健康度">
            <Space direction="vertical" size={14} style={{ width: '100%' }}>
              <Statistic title="池内邮箱总数" value={stats?.outlook_pool?.total ?? 0} />
              <Statistic title="启用数量" value={stats?.outlook_pool?.enabled ?? 0} />
              <Statistic title="OAuth 完整数量" value={stats?.outlook_pool?.oauth ?? 0} />
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
