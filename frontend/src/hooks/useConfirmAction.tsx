import { App } from 'antd';
import type { ReactNode } from 'react';
import { ErrorAlert } from '../components/Common';

export function useConfirmAction() {
  const { modal } = App.useApp();
  return ({ title, content, action, okText = '确认删除' }: {
    title: string; content: ReactNode; action: () => Promise<void>; okText?: string;
  }) => {
    const dialog = modal.confirm({
      title, content, okText, cancelText: '取消', okButtonProps: { danger: true },
      onOk: (close: () => void) => {
        dialog.update({ okButtonProps: { danger: true, loading: true },
          cancelButtonProps: { disabled: true }, closable: false, keyboard: false });
        void action().then(close).catch((failure: unknown) => {
          const detail = failure instanceof Error ? failure.message : String(failure);
          dialog.update({ okButtonProps: { danger: true, loading: false },
            cancelButtonProps: { disabled: false }, closable: true, keyboard: true,
            content: <>{content}<ErrorAlert error={detail} /></>,
          });
        });
      },
    });
    return dialog;
  };
}
