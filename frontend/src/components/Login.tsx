import { Button, Card, Form, Input, Typography } from 'antd';
import { useAction } from '../hooks/useAction';
import { ErrorAlert } from './Common';
export function Login({
  login,
  error,
}: {
  login: (username: string, password: string) => Promise<void>;
  error: string;
}) {
  const action = useAction();
  return (
    <div className="login-page">
      <Card title="登录 Review Lab">
        <Typography.Paragraph type="secondary">
          使用部署时配置的工作台账号。登录信息仅保留在当前页面内存中。
        </Typography.Paragraph>
        <Form
          layout="vertical"
          onFinish={(values: { username: string; password: string }) =>
            void action.run(() => login(values.username, values.password))
          }
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <ErrorAlert error={error || action.error} />
          <Button type="primary" htmlType="submit" loading={action.busy} block>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
