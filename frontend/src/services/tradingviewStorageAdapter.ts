import { apiService } from './apiService';

type SettingsMap = Record<string, string>;

const THEME_LOCKED_SETTING_PATTERNS = [
  'paneProperties.background',
  'paneProperties.backgroundType',
  'paneProperties.vertGridProperties.color',
  'paneProperties.horzGridProperties.color',
  'paneProperties.crossHairProperties.color',
  'scalesProperties.backgroundColor',
  'scalesProperties.textColor',
  'scalesProperties.lineColor',
];

const isThemeLockedSettingKey = (key: string): boolean => {
  const normalized = String(key || '').trim();
  if (!normalized) return false;
  return THEME_LOCKED_SETTING_PATTERNS.some((pattern) => normalized.includes(pattern));
};

const sanitizeThemeLockedSettings = (settings: SettingsMap): SettingsMap => {
  if (!settings || typeof settings !== 'object') return {};
  const filtered: SettingsMap = {};
  Object.entries(settings).forEach(([key, value]) => {
    if (isThemeLockedSettingKey(key)) return;
    filtered[key] = value;
  });
  return filtered;
};

type ChartMeta = {
  id: number;
  name: string;
  symbol: string;
  resolution: string;
  timestamp: number;
};

const enc = (v: string): string => encodeURIComponent(String(v || ''));

export const getTvInitialSettings = async (): Promise<SettingsMap> => {
  try {
    const response = await apiService.get('/chart/storage/settings');
    const settings = response?.settings && typeof response.settings === 'object' ? response.settings : {};
    return sanitizeThemeLockedSettings(settings);
  } catch {
    return {};
  }
};

export const createTvSettingsAdapter = (initialSettings: SettingsMap = {}) => {
  return {
    initialSettings: sanitizeThemeLockedSettings(initialSettings),
    setValue: (key: string, value: string) => {
      if (isThemeLockedSettingKey(key)) return;
      apiService.put(`/chart/storage/settings/${enc(key)}`, { value: String(value ?? '') }).catch(() => {});
    },
    removeValue: (key: string) => {
      if (isThemeLockedSettingKey(key)) return;
      apiService.delete(`/chart/storage/settings/${enc(key)}`).catch(() => {});
    },
  };
};

export const createTvSaveLoadAdapter = () => {
  return {
    getAllCharts: async (): Promise<ChartMeta[]> => {
      const response = await apiService.get('/chart/storage/charts');
      return Array.isArray(response?.charts) ? response.charts : [];
    },

    removeChart: async (id: number | string): Promise<void> => {
      await apiService.delete(`/chart/storage/charts/${enc(String(id))}`);
    },

    saveChart: async (chartData: { id?: string; name: string; symbol: string; resolution: string; content: string }): Promise<string> => {
      const response = await apiService.post('/chart/storage/charts', chartData);
      return String(response?.id || '');
    },

    getChartContent: async (chartId: number): Promise<string> => {
      const response = await apiService.get(`/chart/storage/charts/${enc(String(chartId))}`);
      return String(response?.content || '');
    },

    getAllStudyTemplates: async (): Promise<Array<{ name: string }>> => {
      const response = await apiService.get('/chart/storage/study-templates');
      return Array.isArray(response?.templates) ? response.templates : [];
    },

    removeStudyTemplate: async (studyTemplateInfo: { name: string }): Promise<void> => {
      await apiService.delete(`/chart/storage/study-templates/${enc(studyTemplateInfo?.name || '')}`);
    },

    saveStudyTemplate: async (studyTemplateData: { name: string; content: string }): Promise<void> => {
      await apiService.post('/chart/storage/study-templates', {
        name: studyTemplateData?.name || '',
        content: studyTemplateData?.content || '',
      });
    },

    getStudyTemplateContent: async (studyTemplateInfo: { name: string }): Promise<string> => {
      const response = await apiService.get(`/chart/storage/study-templates/${enc(studyTemplateInfo?.name || '')}`);
      return String(response?.content || '');
    },

    getDrawingTemplates: async (toolName: string): Promise<string[]> => {
      const response = await apiService.get(`/chart/storage/drawing-templates/${enc(toolName || '')}`);
      return Array.isArray(response?.templates) ? response.templates : [];
    },

    loadDrawingTemplate: async (toolName: string, templateName: string): Promise<string> => {
      const response = await apiService.get(`/chart/storage/drawing-templates/${enc(toolName || '')}/${enc(templateName || '')}`);
      return String(response?.content || '');
    },

    removeDrawingTemplate: async (toolName: string, templateName: string): Promise<void> => {
      await apiService.delete(`/chart/storage/drawing-templates/${enc(toolName || '')}/${enc(templateName || '')}`);
    },

    saveDrawingTemplate: async (toolName: string, templateName: string, content: string): Promise<void> => {
      await apiService.post('/chart/storage/drawing-templates', {
        tool_name: toolName || '',
        template_name: templateName || '',
        content: content || '',
      });
    },
  };
};
