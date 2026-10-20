import { useCallback, useMemo, useReducer } from 'react'
import type { Project } from '../types/kanban'

type ProjectAction =
  | { type: 'SET'; projects: Project[]; activeId: string }
  | { type: 'ADD'; project: Project }
  | { type: 'REMOVE'; id: string }
  | { type: 'SET_ACTIVE'; id: string }

interface ProjectState {
  projects: Project[]
  activeProjectId: string
}

function normalizeProjectId(id: string): string {
  return id.trim() || 'default'
}

function projectReducer(state: ProjectState, action: ProjectAction): ProjectState {
  switch (action.type) {
    case 'SET':
      return { projects: action.projects, activeProjectId: normalizeProjectId(action.activeId) }
    case 'ADD':
      if (state.projects.some(p => p.id === action.project.id)) return state
      return { ...state, projects: [...state.projects, action.project] }
    case 'REMOVE': {
      const filtered = state.projects.filter(p => p.id !== action.id)
      const newActive = state.activeProjectId === action.id ? 'default' : state.activeProjectId
      return { projects: filtered, activeProjectId: newActive }
    }
    case 'SET_ACTIVE':
      return { ...state, activeProjectId: normalizeProjectId(action.id) }
    default:
      return state
  }
}

export interface ProjectStoreState {
  projects: Project[]
  activeProjectId: string
  initFromBackend: (projects: Project[], activeId: string) => void
  addProject: (project: Project) => void
  removeProject: (id: string) => void
  setActiveProject: (id: string) => void
}

export function useProjectStore(): ProjectStoreState {
  const [state, dispatch] = useReducer(projectReducer, {
    projects: [{ id: 'default', name: 'default' }],
    activeProjectId: 'default',
  })

  const initFromBackend = useCallback((projects: Project[], activeId: string) => {
    dispatch({ type: 'SET', projects, activeId })
  }, [])

  const addProject = useCallback((project: Project) => {
    dispatch({ type: 'ADD', project })
  }, [])

  const removeProject = useCallback((id: string) => {
    dispatch({ type: 'REMOVE', id })
  }, [])

  const setActiveProject = useCallback((id: string) => {
    dispatch({ type: 'SET_ACTIVE', id })
  }, [])

  return useMemo(() => ({
    projects: state.projects,
    activeProjectId: state.activeProjectId,
    initFromBackend,
    addProject,
    removeProject,
    setActiveProject,
  }), [state, initFromBackend, addProject, removeProject, setActiveProject])
}
