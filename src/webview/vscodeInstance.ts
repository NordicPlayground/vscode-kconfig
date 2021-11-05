import { GenericMessage } from './messages';

// @ts-ignore
export const vscode = window.acquireVsCodeApi<GenericMessage>();
