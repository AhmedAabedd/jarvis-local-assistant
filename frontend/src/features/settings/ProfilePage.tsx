import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, X } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { Profile } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { keys, useProfile } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'

export function ProfilePage() {
  const query = useProfile(),
    client = useQueryClient(),
    [success, setSuccess] = useState(''),
    [draft, setDraft] = useState<Profile | null>(null),
    [savedProfile, setSavedProfile] = useState<Profile | null>(null)
  useEffect(() => {
    if (!query.data) return
    setDraft({ ...query.data })
    setSavedProfile({ ...query.data })
  }, [query.data])
  const dirty = Boolean(
    draft && savedProfile && JSON.stringify(draft) !== JSON.stringify(savedProfile),
  )
  const update = useMutation({
    mutationFn: api.profile.update,
    onSuccess: async (saved) => {
      client.setQueryData(keys.profile, saved)
      setDraft({ ...saved })
      setSavedProfile({ ...saved })
      await client.invalidateQueries({ queryKey: keys.profile })
      await client.invalidateQueries({ queryKey: keys.overview })
      setSuccess('Profile saved.')
    },
  })
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!draft) return
    setSuccess('')
    update.mutate(draft)
  }
  return (
    <>
      <PageHeader
        title="Profile"
        description="Personalize names, location, and language"
        actions={
          dirty && draft && savedProfile ? (
            <>
              <Button
                className="resource-header-action"
                icon={<X size={15} />}
                disabled={update.isPending}
                onClick={() => {
                  update.reset()
                  setSuccess('')
                  setDraft({ ...savedProfile })
                }}
                aria-label="Discard changes"
                title="Discard changes"
              />
              <Button
                variant="primary"
                icon={<Save size={14} />}
                busy={update.isPending}
                type="submit"
                form="profile-form"
              >
                Save profile
              </Button>
            </>
          ) : undefined
        }
      />
      {query.isLoading ? (
        <Loading />
      ) : (
        <div className="page-content">
          <Card
            title="Assistant profile"
            description="These details are included in runtime instructions and visible throughout the interface."
          >
            <form id="profile-form" className="card__body form-grid" onSubmit={submit}>
              <Field label="Your name" hint="How the assistant identifies and addresses you.">
                <input
                  name="user_name"
                  value={draft?.user_name || ''}
                  onChange={(event) =>
                    setDraft((current) =>
                      current ? { ...current, user_name: event.target.value } : current,
                    )
                  }
                  maxLength={80}
                  required
                />
              </Field>
              <Field label="Assistant name" hint="Shown in the interface and used in responses.">
                <input
                  name="assistant_name"
                  value={draft?.assistant_name || ''}
                  onChange={(event) =>
                    setDraft((current) =>
                      current ? { ...current, assistant_name: event.target.value } : current,
                    )
                  }
                  maxLength={80}
                  required
                />
              </Field>
              <Field full label="Location" hint="Used for weather, time, and nearby information.">
                <input
                  name="location"
                  value={draft?.location || ''}
                  onChange={(event) =>
                    setDraft((current) =>
                      current ? { ...current, location: event.target.value } : current,
                    )
                  }
                  maxLength={160}
                  required
                />
              </Field>
              <Field full label="Preferred response language">
                <select
                  name="preferred_language"
                  value={draft?.preferred_language || 'auto'}
                  onChange={(event) =>
                    setDraft((current) =>
                      current ? { ...current, preferred_language: event.target.value } : current,
                    )
                  }
                >
                  <option value="auto">Automatic</option>
                  <option value="en">English</option>
                  <option value="fr">French</option>
                  <option value="ar">Arabic</option>
                </select>
              </Field>
              <div className="field--full">
                <Feedback
                  message={update.error instanceof Error ? update.error.message : success}
                  kind={success ? 'success' : 'error'}
                />
              </div>
            </form>
          </Card>
        </div>
      )}
    </>
  )
}
