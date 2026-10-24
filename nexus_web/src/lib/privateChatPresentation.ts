import type { AgentRunMessage } from './types';

// These are adapter diagnostics, not the assistant's response. Keep the raw
// result in execution details; retain user/model-authored Markdown tables.
const telemetry = new Set(['status', 'reply', 'tools executed', 'model requests', 'model', 'turns']);
const preparation = 'Pi is preparing your private workspace.';

export function privateChatMessages(messages: AgentRunMessage[]): AgentRunMessage[] {
  return messages.flatMap(message => {
    if (message.role !== 'assistant') return [message];
    const source = message.content_blocks?.length ? message.content_blocks : [{ type: 'markdown' as const, text: message.content }];
    const blocks = source.flatMap(block => {
      if (block.type === 'fields' && block.items.every(item => telemetry.has(item.label.toLowerCase().trim()))) {
        const reply = block.items.find(item => item.label.toLowerCase().trim() === 'reply')?.value;
        return reply && !source.some(item => item.type === 'markdown' && item.text.trim() === reply.trim()) ? [{ type: 'markdown' as const, text: reply }] : [];
      }
      return block.type === 'markdown' && block.text.trim() === preparation ? [] : [block];
    });
    if (!blocks.length) return [];
    return [{ ...message, content_blocks: blocks }];
  });
}
