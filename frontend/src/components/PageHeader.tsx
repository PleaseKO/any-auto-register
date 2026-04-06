import { Space, Typography } from 'antd'
import type { ReactNode } from 'react'

const { Text } = Typography

type PageHeaderProps = {
  eyebrow?: ReactNode
  title: ReactNode
  description?: ReactNode
  extra?: ReactNode
}

export function PageHeader({ eyebrow, title, description, extra }: PageHeaderProps) {
  return (
    <div
      style={{
        padding: 24,
        borderRadius: 20,
        background: 'linear-gradient(135deg, rgba(99,102,241,0.14) 0%, rgba(16,185,129,0.08) 100%)',
        border: '1px solid rgba(99,102,241,0.14)',
        display: 'flex',
        justifyContent: 'space-between',
        gap: 16,
        alignItems: 'flex-start',
        flexWrap: 'wrap',
      }}
    >
      <div style={{ maxWidth: 760 }}>
        {eyebrow ? (
          <Space size={10} align="center" style={{ marginBottom: 10 }}>
            {typeof eyebrow === 'string' ? <Text strong>{eyebrow}</Text> : eyebrow}
          </Space>
        ) : null}
        <h1 style={{ margin: 0, fontSize: 28, lineHeight: 1.15 }}>{title}</h1>
        {description ? (
          <p style={{ margin: '10px 0 0', color: '#7a8ba3', maxWidth: 720 }}>{description}</p>
        ) : null}
      </div>
      {extra ? <div>{extra}</div> : null}
    </div>
  )
}
