import { useCallback, useEffect, useState } from 'react'
import { Card, Table, Select, Button, Tag, Space, Popconfirm, Typography, message } from 'antd'
import type { TableColumnsType } from 'antd'
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'
import { TaskLogPanel } from '@/components/TaskLogPanel'

const { Text } = Typography

interface TaskLogItem {
  id: number
  created_at: string
  platform: string
  email: string
  status: 'success' | 'failed'
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

interface RuntimeTaskItem {
  id: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'stopped'
  platform: string
  source: string
  progress: string
  success: number
  skipped: number
  errors: string[]
  error?: string
  created_at?: number
  updated_at?: number
}

export default function TaskHistory() {
  const [logs, setLogs] = useState<TaskLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [platform, setPlatform] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([])
  const [runtimeTasks, setRuntimeTasks] = useState<RuntimeTaskItem[]>([])
  const [runtimeLoading, setRuntimeLoading] = useState(false)
  const [selectedTaskId, setSelectedTaskId] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (platform) params.set('platform', platform)
      const data = await apiFetch(`/tasks/logs?${params}`) as TaskLogListResponse
      setLogs(data.items || [])
      setTotal(data.total || 0)
      setSelectedRowKeys((prev) => prev.filter((key) => data.items.some((item) => item.id === key)))
    } finally {
      setLoading(false)
    }
  }, [platform])

  const loadRuntimeTasks = useCallback(async () => {
    setRuntimeLoading(true)
    try {
      const data = await apiFetch('/tasks') as RuntimeTaskItem[]
      setRuntimeTasks(data || [])
      setSelectedTaskId((current) => {
        if (current && data.some((item) => item.id === current)) return current
        const active = data.find((item) => item.status === 'pending' || item.status === 'running')
        return active?.id || data[0]?.id || ''
      })
    } finally {
      setRuntimeLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    loadRuntimeTasks()
  }, [loadRuntimeTasks])

  useEffect(() => {
    if (!runtimeTasks.some((item) => item.status === 'pending' || item.status === 'running')) return
    const timer = window.setInterval(() => {
      void loadRuntimeTasks()
    }, 2000)
    return () => window.clearInterval(timer)
  }, [runtimeTasks, loadRuntimeTasks])

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return

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
      render: (text: string) => (text ? new Date(text).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 100,
      render: (text: string) => <Tag>{text}</Tag>,
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      render: (text: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{text}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (status: string) => (
        <Tag color={status === 'success' ? 'success' : 'error'}>
          {status === 'success' ? '成功' : '失败'}
        </Tag>
      ),
    },
    {
      title: '错误信息',
      dataIndex: 'error',
      key: 'error',
      render: (text: string) => text || '-',
    },
  ]

  const runtimeColumns: TableColumnsType<RuntimeTaskItem> = [
    {
      title: '任务 ID',
      dataIndex: 'id',
      key: 'id',
      render: (value: string, record: RuntimeTaskItem) => (
        <Button
          type="link"
          style={{ padding: 0, fontFamily: 'monospace' }}
          onClick={() => setSelectedTaskId(record.id)}
        >
          {value}
        </Button>
      ),
    },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 100,
      render: (text: string) => <Tag>{text}</Tag>,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 100,
      render: (text: string) => <Tag color="blue">{text || 'manual'}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag
          color={
            status === 'done'
              ? 'success'
              : status === 'failed'
                ? 'error'
                : status === 'stopped'
                  ? 'warning'
                  : 'processing'
          }
        >
          {status === 'done' ? '完成' : status === 'failed' ? '失败' : status === 'stopped' ? '已停止' : '运行中'}
        </Tag>
      ),
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      width: 100,
    },
    {
      title: '结果',
      key: 'summary',
      render: (_: unknown, record: RuntimeTaskItem) => (
        <span>成功 {record.success} / 跳过 {record.skipped} / 错误 {record.errors?.length || 0}</span>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: (value?: number) => (value ? new Date(value * 1000).toLocaleString('zh-CN') : '-'),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 'bold', margin: 0 }}>任务历史</h1>
          <p style={{ color: '#7a8ba3', marginTop: 4 }}>查看运行中的注册任务与历史执行记录</p>
        </div>
        <Space>
          <Text type="secondary">{total} 条记录</Text>
          {selectedRowKeys.length > 0 && <Text type="success">已选 {selectedRowKeys.length} 条</Text>}
          {selectedRowKeys.length > 0 && (
            <Popconfirm
              title={`确认删除选中的 ${selectedRowKeys.length} 条任务历史？`}
              onConfirm={handleBatchDelete}
            >
              <Button danger icon={<DeleteOutlined />}>
                删除 {selectedRowKeys.length} 条
              </Button>
            </Popconfirm>
          )}
          <Select
            value={platform}
            onChange={(value) => {
              setPlatform(value)
              setSelectedRowKeys([])
            }}
            style={{ width: 120 }}
            options={[
              { value: '', label: '全部平台' },
              { value: 'trae', label: 'Trae' },
              { value: 'cursor', label: 'Cursor' },
            ]}
          />
          <Button icon={<ReloadOutlined spin={loading} />} onClick={load} loading={loading} />
        </Space>
      </div>

      <Card
        title="当前任务列表"
        extra={
          <Button
            icon={<ReloadOutlined spin={runtimeLoading} />}
            onClick={loadRuntimeTasks}
            loading={runtimeLoading}
          >
            刷新
          </Button>
        }
      >
        <Table
          rowKey="id"
          columns={runtimeColumns}
          dataSource={runtimeTasks}
          loading={runtimeLoading}
          pagination={false}
          locale={{ emptyText: '暂无运行中或近期任务' }}
        />
      </Card>

      {selectedTaskId && (
        <Card title={`任务进度 - ${selectedTaskId}`}>
          <TaskLogPanel taskId={selectedTaskId} onDone={() => { void loadRuntimeTasks() }} />
        </Card>
      )}

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={logs}
          loading={loading}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys as number[]),
          }}
          pagination={{ pageSize: 20, showSizeChanger: false }}
        />
      </Card>
    </div>
  )
}
